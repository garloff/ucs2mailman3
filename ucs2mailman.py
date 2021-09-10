#!/usr/bin/env python3
#
# Feed mailman3 MLs with group members from UCS LDAP
#
# (c) garloff@osb-alliance.com, 9/2021
# SPDX-License-Identifier: AGPL-3
#

import os, sys, subprocess, re, getopt, pwd
from operator import methodcaller, attrgetter
import bisect

#mailmanBin = "/usr/lib/mailman3/bin/mailman"
udmBin = "/usr/sbin/udm"

debug = False
testMode = False
testMode2 = False
filterList = ""
admin = ""
prefix = ""

from public import public

from contextlib import ExitStack
from mailman.config import config
from mailman.core.i18n import _
from mailman.core.initialize import initialize
from mailman.database.transaction import transaction

from mailman.interfaces.domain import IDomainManager
#from mailman.interfaces.domain import IMailingList
from mailman.interfaces.subscriptions import ISubscriptionManager
from mailman.interfaces.usermanager import IUserManager
from mailman.interfaces.listmanager import IListManager
from mailman.interfaces.mailinglist import SubscriptionPolicy
from mailman.interfaces.styles import IStyleManager
from mailman.interfaces.member import MemberRole
from mailman.app.lifecycle import create_list
from zope.component import getUtility
from mailman.testing.helpers import subscribe

from mailman.utilities.datetime import now

def ldapParse(lns, attr):
    "Search lines in lns for LDAP attribute attr: and return array"
    ans = []
    srch = " %s: " % attr
    for ln in lns:
        if srch in ln:
            ix = ln.find(':')
            ans.append(ln[ix+2:])
            #print("%s" % ln[ix+2:])
        if len(ln) == 0:
            break
    return ans

def ldapAttr(ln, attr):
    "Search line for attribute attr=[...], return array"
    ans = []
    srch = "%s=" % attr
    assert(srch in ln)
    ix = ln.find(srch)
    while ix >= 0:
        ix += len(srch)
        en = ln[ix:].find(",")
        if en > 0:
            ans.append(ln[ix:ix+en])
            ix += en
        else:
            ans.append(ln[ix:])
            break
        ln = ln[ix:]
        ix = ln.find(srch)
    return ans


class ldapGroup:
    "Representation of LDAP group"
    def __init__(self, lines):
        self.cn = None
        self.mailAddr = None
        cn = ldapAttr(lines[0], "cn")
        if cn:
            self.cn = cn[0]
        else:
            print("ERROR: No cn= in %s" % lines[0])
        mailAddr = ldapParse(lines, "mailAddress")
        if mailAddr and mailAddr[0] != "None":
            self.mailAddr = prefix+mailAddr[0]
        users = ldapParse(lines, "users")
        self.userList = []
        if users:
            for ln in users:
                self.userList.append((ldapAttr(ln, "uid")[0] + "@" + str.join(".", ldapAttr(ln, "dc"))).lower())


class ldapUser:
    def __init__(self, lines):
        self.uid = ldapAttr(lines[0], "uid")[0]
        self.primMail = self.uid + "@" + str.join(".", ldapAttr(lines[0], "dc"))
        self.dName = ldapParse(lines, "displayName")[0]
        self.mails = []
        self.groups = []
        self.mails.append(ldapParse(lines, "PasswordRecoveryEmail")[0])
        for mail in ldapParse(lines, "e-mail"):
            if not mail in self.mails:
                self.mails.append(mail)
        mail = ldapParse(lines, "mailForwardAddress")
        if mail and not mail[0] in self.mails:
            self.mails.append(mail[0])
        groups = ldapParse(lines, "groups")
        for gr in groups:
            self.groups.append(ldapAttr(gr, "cn")[0])
    def sortKey(self):
        return self.primMail.lower()


class mList:
    def __init__(self, ml):
        self.mlName = ml.posting_address
        self.mlMembers = []
        self.mlNonMembers = []
        members = ml.get_roster(MemberRole.member).members
        for member in members:
            if member.address:
                self.mlMembers.append(member.address.email)
            else:
                print(" member %s without address?" % member, file = sys.stderr)
        members = ml.get_roster(MemberRole.nonmember).members
        for member in members:
            self.mlNonMembers.append(member.address.email)
    def __repr__(self):
        return "%s: Members: %s, NonMembers: %s" % (self.mlName, self.mlMembers, self.mlNonMembers)


def replDomain(email, newdom):
    "return email with the domain replaced by newdom"
    domix = email.find("@")
    assert(domix > 0)
    return email[:domix+1] + newdom


def collectGroups(translate):
    f = subprocess.Popen(("%s" % udmBin,"groups/group", "list"), text = True, stdout = subprocess.PIPE)
    lines = list(map(lambda x: x.rstrip('\n'), f.stdout.readlines()))
    if f.wait():
        assert(false)
    groups = []
    last = 0
    for lno in range(0, len(lines)):
        if not lines[lno]:
            if lno == last:
                last += 1
            else:
                groups.append(ldapGroup(lines[last:lno]))
                last = lno+1
    if len(lines) and last != len(lines):
        groups.append(ldapGroup(lines[last:]))
    groups = list(filter(lambda x: x.mailAddr is not None, groups))
    if translate:
        for g in groups:
            g.mailAddr = replDomain(g.mailAddr, translate)
    return groups

def collectUsers():
    f = subprocess.Popen(("%s" % udmBin,"users/user", "list"), text = True, stdout = subprocess.PIPE)
    lines = list(map(lambda x: x.rstrip('\n'), f.stdout.readlines()))
    if f.wait():
        assert(false)
    users = []
    last = 0
    for lno in range(0, len(lines)):
        if not lines[lno]:
            if lno == last:
                last += 1
            else:
                users.append(ldapUser(lines[last:lno]))
                last = lno+1
    if len(lines) and last != len(lines):
        users.append(ldapUser(lines[last:]))
    return sorted(users, key = ldapUser.sortKey)

def findUser(userList, primMail):
    #sList = map(lambda x: x.sortKey(), userList)
    primMail = primMail.lower()
    sList = [u.sortKey() for u in userList]
    ix = bisect.bisect_left(sList, primMail)
    if ix == len(sList) or sList[ix] != primMail:
        return None
    return userList[ix]

# global MM context
domManager = None

def collectMMLists():
    "Get all mailings lists from Mailman3"
    global domManager
    initialize()
    domManager = getUtility(IDomainManager)
    lists = []
    for domain in domManager:
        for ml in domain.mailing_lists:
            lists.append(mList(ml))
    return lists

def getML(adr):
    for domain in domManager:
        for ml in domain.mailing_lists:
            if ml.posting_address == adr:
                return ml
    return None

def allMails(lUsers, userMail):
    "return list of extra mails from user"
    user = findUser(lUsers, userMail)
    if user:
        return user.mails
    else:
        return []

userManager = None

def createML(lGroup):
    "Create mailing list with default settings from ldapGroup lGroup"
    global userManager
    if not userManager:
        userManager = getUtility(IUserManager)
    assert(admin)
    #domName = lGroup.mailAddr[lGroup.mailAddr.find("@")+1:]
    #domain = domManager[domName]
    mList = create_list(lGroup.mailAddr)
    assert(mList)
    getUtility(IStyleManager).get('legacy-default').apply(mList)
    mList.subscription_policy = SubscriptionPolicy.open
    # admin MUST exist (and have confirmed mailaddress)
    adminUser = userManager.get_user(admin)
    assert(adminUser)
    adminAddr = userManager.get_address(admin)
    adminUser.preferred_address = adminAddr
    mList.subscribe(adminUser, MemberRole.owner)
    mList.subscribe(adminUser, MemberRole.moderator)
    # Close for subscription/unsubscription
    mList.subscription_policy = SubscriptionPolicy.moderate
    mList.unsubscription_policy = SubscriptionPolicy.confirm
    # Settings: Invisible
    mList.advertised = False
    mList.description = "LDAP group %s" % lGroup.cn
    return mList

def mlSubscribe(ml, mAddr, dName):
    "Subscribe mAddr to ml as member and consider confirmed"
    global userManager
    if not userManager:
        userManager = getUtility(IUserManager)
    user = userManager.get_user(mAddr)
    if not user:
        user = userManager.make_user(mAddr, dName)
    preferred = list(user.addresses)[0]
    preferred.verified_on = now()
    user.preferred_address = preferred
    ml.subscription_policy = SubscriptionPolicy.open
    ml.subscribe(user, MemberRole.member)
    ml.subscription_policy = SubscriptionPolicy.moderate

def mlAddSubscription(ml, mainAddr, addtlAddr):
    "Add addtlAddr to mainAddr user and add to ml as nonmember"
    global userManager
    if not userManager:
        userManager = getUtility(IUserManager)
    mainUser = userManager.get_user(mainAddr)
    newAddr = userManager.get_address(addtlAddr)
    if not newAddr:
        newAddr = userManager.create_address(addtlAddr)
        newAddr.verified_on = now()
        mainUser.link(newAddr)
    ml.subscription_policy = SubscriptionPolicy.open
    newmember = ml.subscribe(newAddr, MemberRole.nonmember)
    ml.subscription_policy = SubscriptionPolicy.moderate
    # Set moderation_action from new nonmember to default_member_action
    newmember.moderation_action = ml.default_member_action

def reconcile(lGroups, lUsers, mLists):
    "Reconcile Mailman3 lists with input from LDAP"
    # Now: Reconciliation steps
    mListDict = { x.mlName: x for x in mLists }
    for lg in lGroups:
        ml = None
        mml = None
        if filterList and lg.mailAddr != filterList:
            continue
        # (1) Create new lists from LDAP Groups
        if lg.mailAddr not in mListDict:
            print("Mailing list %s missing" % lg.mailAddr)
            if testMode:
                continue
            #  (1a) Create ML with useful defaults
            mml = createML(lg)
            ml = mList(mml)
            mLists.append(ml)
            mListDict[ml.mlName] = ml
        else:
            # (2) For existing lists:
            ml = mListDict[lg.mailAddr]
            mml = getML(lg.mailAddr)
        for luser in lg.userList:
            user = findUser(lUsers, luser)
            #  (2a) Any subscribers (members) missing?
            if luser.lower() not in ml.mlMembers:
                print("Subscriber %s <%s> to list %s missing" % (user.dName, luser, lg.mailAddr))
                if not testMode2:
                    mlSubscribe(mml, luser, user.dName)
            #  (2b) Any nonMembers (whitelisted posters) missing?
            for addtlMail in allMails(lUsers, luser):
                if addtlMail not in ml.mlNonMembers:
                    print(" Whitelist entry %s (user %s) to list %s missing" % (addtlMail, luser, lg.mailAddr))
                    if not testMode2:
                        mlAddSubscription(mml, luser, addtlMail)
        #  (2c) Any extra subscribers (members) that should be removed?
        for member in ml.mlMembers:
            if member not in lg.userList:
                print("Subscriber %s should be removed from list %s" % (member, lg.mailAddr))
                # TODO
        # Note: Extra nonMembers are OK
    # Note: Extra lists are OK
    pass

def usage(ret):
    print("Usage: ucs2mailman.py [-d] [-n] [-h] [-a adminMail] [-t DOMAIN] [-p PREFIX] [-f LIST]")
    print("(c) Kurt Garloff <garloff@osb-alliance.com>, 9/2021, AGPL-v3")
    print("ucs2mailman.py calls udm to get lists of groups and users from UCS LDAP.")
    print("It then gets the mailing list with subscribers and nonMembers from Mailman3.")
    print("It then ensures that all LDAP groups with mailAddress have a corresponding")
    print("MailMan3 mailing list (ML) and that all group members are subscribed to it")
    print("and all other known mail addresses from subscribers are added as non-members")
    print("to allow them to have unmoderated posting. Extra subscribers (not in LDAP group)")
    print("will be removed, extra non-members are left alone. Extra lists are also left alone.")
    print("Note that you will typically need to run this as root (with sudo).")
    print("Options: -d   => debug output")
    print(" -n           => don't do any changes to MailMan, just print actions")
    print(" -h           => output this help an exit")
    print(" -a adminMail => use this user as owner/moderator for newly created lists (must exist!)")
    print(" -t DOMAIN    => replace mailAddress domain with DOMAIN for the ML")
    print(" -p PREFIX    => prepend prefix to mailing list names")
    print(" -f LIST      => only process mailing list LIST (matching happens after applying -p/-t)")
    sys.exit(ret)

def main(argv):
    global debug, testMode, testMode2, admin, filterList, prefix
    translate = None
    # TODO: Use getopt
    try:
        (optlist, args) = getopt.gnu_getopt(argv[1:], 'hdnNa:t:f:p:')
    except getopt.GetoptError as exc:
        print(exc)
        usage(1)
    for (opt, arg) in optlist:
        if opt == "-d":
            debug = True
            continue
        if opt == "-h":
            usage(0)
        if opt == "-n":
            testMode = True
            testMode2 = True
            continue
        if opt == "-N":
            testMode2 = True
            continue
        if opt == "-a":
            admin = arg
            continue
        if opt == "-t":
            translate = arg
            continue
        if opt == "-p":
            prefix = arg
            continue
        if opt == "-f":
            filterList = arg
            continue

    lGroups = collectGroups(translate)
    lUsers = collectUsers()
    # Debugging: Dump info
    for lg in lGroups:
        assert(lg.mailAddr is not None)
        if debug:
            print("LDAP(%s): %s" % (lg.cn, lg.mailAddr))
        for us in lg.userList:
            if debug:
                print(" %s: %s" % (us, allMails(lUsers, us)))
        if debug:
            print()

    # Switch to mailman user ID
    os.setegid(pwd.getpwnam('list').pw_gid)
    os.seteuid(pwd.getpwnam('list').pw_uid)
    # Read existing MLs and determine needed changes
    mLists = collectMMLists()
    if debug:
        for ml in mLists:
            print(ml)

    reconcile(lGroups, lUsers, mLists)

    with ExitStack() as resources:
        # If given a bogus subcommand, the database won't have been
        # initialized so there's no transaction to commit.
        if config.db is not None:
            resources.enter_context(transaction())

    return 0


if __name__ == "__main__":
    main(sys.argv)
