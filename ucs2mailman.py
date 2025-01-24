#!/usr/bin/env python3
#
# Feed mailman3 MLs with group members from UCS LDAP
#
# (c) garloff@osb-alliance.com, 9/2021
# SPDX-License-Identifier: AGPL-3.0-or-later
#

import os, sys, subprocess, re, getopt, pwd
from operator import methodcaller, attrgetter
import bisect
import base64

#mailmanBin = "/usr/lib/mailman3/bin/mailman"
udmBin = "/usr/sbin/udm"

debug = False
testMode = False
testMode2 = False
noDelete = False
nested = 1
filterList = []
excludeList = []
replaceList = []
admin = ""
prefix = ""
userFile = ""
groupFile = ""

from public import public

from contextlib import ExitStack
from mailman.config import config
from mailman.core.i18n import _
from mailman.core.initialize import initialize
from mailman.database.transaction import transaction

from zope.component import getUtility
from mailman.interfaces.usermanager import IUserManager
from mailman.interfaces.listmanager import IListManager
from mailman.interfaces.subscriptions import ISubscriptionManager
from mailman.interfaces.mailinglist import SubscriptionPolicy
from mailman.interfaces.domain import IDomainManager
#from mailman.interfaces.domain import IMailingList
from mailman.interfaces.styles import IStyleManager
from mailman.interfaces.member import MemberRole
from mailman.interfaces.address import AddressAlreadyLinkedError
from mailman.app.lifecycle import create_list
from mailman.testing.helpers import subscribe

from mailman.utilities.datetime import now

def ldapParse(lns, attr):
    "Search lines in lns for LDAP attribute attr: and return array"
    ans = []
    srch = "%s:" % attr
    for ln in lns:
        ix = ln.find(srch)
        if ix == -1 or (ix > 0 and ln[ix-1] not in (" ", "\t")):
            continue
        ix += len(srch)
        if ln[ix] == ":":
            ans.append(base64.b64decode(ln[ix+2:]).decode("utf-8"))
            # Base64 decode
        else:
            ans.append(ln[ix+1:])
        #print("%s" % ln[ix+2:])
        if len(ln) == 0:
            break
    return ans

def ldapAttr(ln, attr):
    "Search line for attribute attr=[...], return array"
    ans = []
    srch = "%s=" % attr
    if not srch in ln:
        print("WARNING: Expected %s in %s" % (srch, ln))
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

class ldapUser:
    "Represents interesting fields from LDAP user list"
    def __init__(self, lines):
        self.uid = ldapAttr(lines[0], "uid")[0]
        self.primMail = self.uid + "@" + str.join(".", ldapAttr(lines[0], "dc"))
        #if debug:
        #    print("Parsing %i lines for uid %s <%s>" % (len(lines), self.uid, self.primMail))
        dName = ldapParse(lines, "displayName")
        if dName:
            self.dName = dName[0]
        else:
            print("WARN: uid %s <%s> without displayName!" % (self.uid, self.primMail))
            self.dName = ""
        # Collect mail addresses
        self.mails = []
        for tag in ("PasswordRecoveryEmail", "mailForwardAddress", "e-mail", "mail"):
            for mail in ldapParse(lines, tag):
                if mail and not mail in self.mails and not mail == self.primMail and not mail == "None":
                    self.mails.append(mail)
        # Collect group membership (for consistency checking, currently unused)
        self.groups = []
        groups = ldapParse(lines, "groups")
        for gr in groups:
            self.groups.append(ldapAttr(gr, "cn")[0])
    def sortKey(self):
        return self.primMail.lower()

def findUser(lUsers, primMail):
    "Find user in sorted userList by primMail"
    #sList = map(lambda x: x.sortKey(), userList)
    primMail = primMail.lower()
    sList = [u.sortKey() for u in lUsers]
    ix = bisect.bisect_left(sList, primMail)
    if ix == len(sList) or sList[ix] != primMail:
        return None
    return lUsers[ix]


class ldapGroup:
    "Representation of LDAP group"
    def __init__(self, lines, lUsers):
        self.cn = None
        self.mailAddr = None
        cn = ldapAttr(lines[0], "cn")
        if cn:
            self.cn = cn[0]
        else:
            print("ERROR: No cn= in %s" % lines[0])
        mailAddr = ldapParse(lines, "mailAddress")
        if not mailAddr:
            mailAddr = ldapParse(lines, "mailPrimaryAddress")
        if mailAddr and mailAddr[0] != "None":
            self.mailAddr = prefix+mailAddr[0]
        self.nestedGroups = ldapParse(lines, "nestedGroup")
        users = ldapParse(lines, "users")
        if not users:
            users = ldapParse(lines, "uniqueMember")
        self.userList = []
        if users:
            for ln in users:
                if ln.find("uid=") == -1:
                    continue
                userMail = (ldapAttr(ln, "uid")[0] + "@" + str.join(".", ldapAttr(ln, "dc"))).lower()
                userObj = findUser(lUsers, userMail)
                if not userObj:
                    print("ERROR: User %s not found in UserList" % userMail)
                assert(userObj)
                self.userList.append(userObj)


class mList:
    "Mailman mailing list members"
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


def collectUsers():
    "Read user list from LDAP"
    if userFile:
        f = open(userFile, "r")
        lines = map(lambda x: x.rstrip('\n'), f.readlines())
        lines = list(filter(lambda x: not x or x[0] != "#", lines))
    else:
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
                #if lines[last][:6] != "search":
                if lines[last].find("uid=") != -1:
                    users.append(ldapUser(lines[last:lno]))
                last = lno+1
    #if len(lines) and last != len(lines) and lines[last][:6] != "search":
    if len(lines) and last != len(lines) and lines[last].find("uid=") != -1:
        users.append(ldapUser(lines[last:]))
    return sorted(users, key = ldapUser.sortKey)


def collectGroups(lUsers, translate = None):
    "Read group list from LDAP"
    if groupFile:
        f = open(groupFile, "r")
        lines = map(lambda x: x.rstrip('\n'), f.readlines())
        lines = list(filter(lambda x: not x or x[0] != "#", lines))
    else:
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
                #if lines[last][:6] != "search":
                if lines[last].find("cn=") != -1:
                    groups.append(ldapGroup(lines[last:lno], lUsers))
                last = lno+1
    #if len(lines) and last != len(lines) and lines[last][:6] != "search":
    if len(lines) and last != len(lines) and lines[last].find("cn=") != -1:
        groups.append(ldapGroup(lines[last:]))
    groups = list(filter(lambda x: x.mailAddr is not None, groups))
    for g in groups:
        for rpl in replaceList:
            if g.mailAddr == rpl[0]:
                g.mailAddr = rpl[1]
                continue
        if translate:
            g.mailAddr = replDomain(g.mailAddr, translate)
    return groups

def findGroup(lGroups, cn):
    shortCN = ldapAttr(cn, "cn")[0]
    for grp in lGroups:
        if grp.cn == shortCN:
            return grp
    print("WARNING: Referenced nested group \"%s\" not found" % cn, file=sys.stderr)
    return None

def addtoGroup(lGroups, grp, ngrp, nest):
    "Add members of ngrp to grp"
    # Special function: Subscribe groups to groups
    if nest < 0:
        if ngrp.mailAddr:
            print(ngrp.mailAddr)
            user = ldapUser(("uid=%s,dc=%s" % tuple(ngrp.mailAddr.split("@")),))
            user.dName = "%s mailing list" % ngrp.mailAddr
            user.primMail = ngrp.mailAddr
            grp.userList.append(user)
        return
    # Add missing users
    for user in ngrp.userList:
        if not user in grp.userList:
            grp.userList.append(user)
    # Recursion
    if nest > 0:
        for nnGrp in ngrp.nestedGroups:
            if nngrp != grp.cn:
                nnGroup = findGroup(lGroups, nnGrp)
                addtoGroup(lGroups, grp, nnGroup, nest-1)


def recurseNestedGroups(lUsers, lGroups, nesting):
    "Include users from nested groups"
    for group in lGroups:
        for nGrp in group.nestedGroups:
            nGroup = findGroup(lGroups, nGrp)
            if nGroup and group.mailAddr:
                addtoGroup(lGroups, group, nGroup, nesting-1)

# global MM context
domManager = None
userManager = None

def collectMMLists():
    "Get all mailings lists from Mailman3"
    global domManager
    domManager = getUtility(IDomainManager)
    lists = []
    for domain in domManager:
        for ml in domain.mailing_lists:
            lists.append(mList(ml))
    return lists

def getML(adr):
    "Get mailinglist object with address adr from Mailman3"
    for domain in domManager:
        for ml in domain.mailing_lists:
            if ml.posting_address == adr:
                return ml
    return None

def createML(lGroup):
    "Create mailing list with default settings from ldapGroup lGroup"
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

def findMMUser(lUser):
    "Search MM for user with one of lUser's mail addresses"
    assert(userManager)
    user = userManager.get_user(lUser.primMail)
    if user:
        return user
    for mAdr in lUser.mails:
        user = userManager.get_user(mAdr)
        if user:
            return user
    return None

def completeMMUser(mmUser, lUser, dName):
    "Add all mails to mmUser"
    pref = None
    if not mmUser.controls(lUser.primMail.lower()):
        print(" Add primary %s <%s> to User %s" % (dName, lUser.primMail, mmUser))
        if not testMode2:
            newAddr = mmUser.register(lUser.primMail, dName)
            newAddr.verified_on = now()
            if not mmUser.preferred_address:
                mmUser.preferred_address = newAddr
    for addr in lUser.mails:
        if not mmUser.controls(addr.lower()):
            print(" Add 2ndary %s <%s> to User %s" % (dName, addr, mmUser))
            if debug:
                print("  Already available addresses: %s " % mmUser.addresses)
            if not testMode2:
                try:
                    newAddr = mmUser.register(addr, dName)
                    newAddr.verified_on = now()
                except AddressAlreadyLinkedError as exc:
                    print(" Uhh, already linked ...")
                except BaseException as exc:
                    print("ERROR: %s %s" % (type(exc), exc), file = sys.stderr)
    if not mmUser.preferred_address:
        lUserAddr = list(filter(lambda x: x.email == lUser.primMail.lower(), mmUser.addresses))[0]
        if not testMode2:
            mmUser.preferred_address = lUserAddr

def completeSubscription(mmUser, mmList):
    """Add all missing mails from mmUser to mmList subscriptions;
       if there is no member subscription, make sure we create one,
       preferrably the preferred address. We might need to remove it
       from nonmembers before."""
    memberSubscribed = None
    for mmAddr in mmUser.addresses:
        if mmList.members.get_member(mmAddr.email):
            memberSubscribed = mmAddr
            break
    # We lack a member subscription. Do it!
    mmList.subscription_policy = SubscriptionPolicy.open
    memberSubscr = ""
    if not memberSubscribed:
        # May need to first unsubscribe preferred address as nonmember
        prefMem = mmList.nonmembers.get_member(mmUser.preferred_address.email)
        if prefMem:
            print("  Remove %s as non-member from %s" % (mmUser.preferred_address.email, mmList))
            mmList.unsubscription_policy = SubscriptionPolicy.open
            if not testMode2:
                prefMem.unsubscribe()
            mmList.unsubscription_policy = SubscriptionPolicy.confirm
        print("  Add %s as member to %s" % (mmUser.preferred_address.email, mmList))
        if not testMode2:
            memberSubscribed = mmList.subscribe(mmUser.preferred_address, MemberRole.member)
        else:
            memberSubscr = mmUser.preferred_address.email
    # Now we have a member, add all other addresses as non-members
    for mmAddr in mmUser.addresses:
        if not mmList.members.get_member(mmAddr.email) and not mmList.nonmembers.get_member(mmAddr.email):
            if not mmAddr.email == memberSubscr:
                print("  Add %s as non-member to %s" % (mmAddr.email, mmList))
            if not testMode2:
                mmSubscr = mmList.subscribe(mmAddr, MemberRole.nonmember)
                mmSubscr.moderation_action = mmList.default_member_action
    mmList.subscription_policy = SubscriptionPolicy.moderate

def changePrefMail(mUser, prefMail):
    "mUser should change preferred mail to prefMail"
    for mail in mUser.addresses:
        if mail.email == prefMail:
            #mail.verified_on = now()
            mUser.preferred_address = mail

def changeSubscr2Pref(mList, mUser, unMail):
    "Ensure unMail is no longer member, ensure mUser.preferred_address is"
    # Need to unsubscribe?
    mlMember = mList.members.get_member(unMail)
    if mlMember:
        mList.unsubscription_policy = SubscriptionPolicy.open
        mlMember.unsubscribe()
        mList.unsubscription_policy = SubscriptionPolicy.confirm
    # Already member?
    mlMember = mList.members.get_member(mUser.preferred_address.email)
    if mlMember:
        return
    # Non-member?
    mlMember = mList.nonmembers.get_member(mUser.preferred_address.email)
    if mlMember:
        mList.unsubscription_policy = SubscriptionPolicy.open
        mlMember.unsubscribe()
        mList.unsubscription_policy = SubscriptionPolicy.confirm
    # Subscribe as member
    mList.subscription_policy = SubscriptionPolicy.open
    mSubscr = mList.subscribe(mUser, MemberRole.member)
    #mSubscr.moderation_action = mList.default_member_action
    mList.subscription_policy = SubscriptionPolicy.moderate

def removeAll(mList, mUser):
    mList.unsubscription_policy = SubscriptionPolicy.open
    for mAdr in mUser.addresses:
        mlMember = mList.members.get_member(mAdr.email)
        if mlMember:
            mlMember.unsubscribe()
            continue
        mlMember = mList.nonmembers.get_member(mAdr.email)
        if mlMember:
            mlMember.unsubscribe()
    mList.unsubscription_policy = SubscriptionPolicy.confirm


def reconcile(lGroups, mLists):
    "Reconcile Mailman3 lists with input from LDAP"
    # Now: Reconciliation steps
    mListDict = { x.mlName: x for x in mLists }
    for lg in lGroups:
        ml = None
        mml = None
        # Process filtering
        if filterList and lg.mailAddr not in filterList:
            continue
        if excludeList and lg.mailAddr in excludeList:
            continue
        if debug:
            print("Process list %s" % lg.mailAddr)
        # (1) Create new lists from LDAP Groups
        if lg.mailAddr not in mListDict:
            print(" Mailing list %s missing, create" % lg.mailAddr)
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
        if debug:
            print(" Check for missing subscribers on list %s" % lg.mailAddr)
        for lUser in lg.userList:
            #  (2a) Ensure that user identified by luser is properly subscribed
            #  - subscribed as member with at least one address (preferrably the primary)
            #  - subscribed as nonmember with all other addresses
            # Case (2a1): None of the mail addresses of this user is known to mailman3:
            #  -> Create a new MM user with main address 
            mmUser = findMMUser(lUser)
            if not mmUser:
                print("  Create User %s <%s>" % (lUser.dName, lUser.primMail))
                ml.mlMembers.append(lUser.primMail)
                if not testMode2:
                    mmUser = userManager.make_user(lUser.primMail, lUser.dName)
                    assert(mmUser)
                    pref = list(mmUser.addresses)[0]
                    pref.verified_on = now()
                    mmUser.preferred_address = pref
            # Case (2a2): Some mail addresses are known to MM
            #  -> Add missing addresses to user (if any)
            completeMMUser(mmUser, lUser, lUser.dName)
            #  -> Check subscription and add missing ones (if any)
            completeSubscription(mmUser, mml)
        #  (2c) Any extra subscribers (members) that should be removed?
        if debug and not noDelete:
            print(" Check for spurious subscribers on list %s" % lg.mailAddr)
        extra = []
        for member in ml.mlMembers:
            foundPrim = False
            foundAny = None
            unsubUser = userManager.get_user(member)
            for lgUser in lg.userList:
                if member == lgUser.primMail.lower():
                    foundPrim = True
                    break
                if member in map(lambda x: x.lower(), lgUser.mails):
                    foundPrim = True
                    break
                assert(unsubUser)
                if not unsubUser:
                    continue
                for ml in unsubUser.addresses:
                    if ml.email == lgUser.primMail.lower():
                        foundAny = ml.email
                        continue
                    if not foundAny and ml.email in map(lambda x: x.lower(), lgUser.mails):
                        foundAny = ml.email

            if not foundPrim:
                if not foundAny:
                    print("  Subscriber %s should be removed from list %s" % (member, lg.mailAddr))
                else:
                    print("  Subscriber %s needs to change to %s for list %s" % (member, foundAny, lg.mailAddr))
                if noDelete or testMode2:
                    continue 
                if foundAny:
                    # Case (2c1) We find other MM mail addresses from that user in the group
                    # In this case: Ensure that the preferred_address is one from LDAP
                    # and make sure this one it subscribed as member.
                    changePrefMail(unsubUser, foundAny)
                    changeSubscr2Pref(mml, unsubUser, member)
                else:
                    # Case (2c2) User should be unsubscribed. In this case, try to find other
                    # mails from MM and remove the nonmembers as well. (This may be incomplete
                    # and that's fine.)
                    removeAll(mml, unsubUser)
        # Note: Extra nonMembers are OK
    # Note: Extra lists are OK
    pass

def usage(ret):
    print("Usage: ucs2mailman.py [-d] [-n] [-h] [-a adminMail] [-t DOMAIN] [-p PREFIX]")
    print("       [-r SRC,DST [-r ...] [-f LIST[,LIST]] [-x LIST[,LIST]] [-u FILE] [-g FILE]")
    print("(c) Kurt Garloff <garloff@osb-alliance.com>, 9/2021, AGPL-v3")
    print("ucs2mailman.py calls udm to get lists of groups and users from UCS LDAP.")
    print("Alternatively it can also process ldapsearch output (ldapsearch -o \"ldif-wrap=255\"),")
    print(" see options -u -g to read UDM/ldapsearch output from files.")
    print("It then gets the mailing list with subscribers and nonMembers from Mailman3.")
    print("It then ensures that all LDAP groups with mailAddress have a corresponding")
    print("MailMan3 mailing list (ML) and that all group members are subscribed to it")
    print("and all other known mail addresses from subscribers are added as non-members")
    print("to allow them to have unmoderated posting. Extra subscribers (not in LDAP group)")
    print("will be removed (unless -k is given), extra non-members are left alone.")
    print("Extra lists are also left alone.")
    print("Note that you will typically need to run this as root (with sudo).")
    print("Options: -d     => debug output")
    print(" -n             => don't do any changes to MailMan, just print actions")
    print(" -R N           => recursively include members from nested groups up to level N (default: 1)")
    print(" -k             => keep subscribers, only add, don't delete (but print)")
    print(" -h             => output this help an exit")
    print(" -a adminMail   => use this user as owner/moderator for newly created lists (must exist!)")
    print(" -p PREFIX      => prepend prefix to mailing list names")
    print(" -r SRC,DST     => replace ML name SRC with DST (after -p, skips -t), can be used multiple times")
    print(" -t DOMAIN      => replace mailAddress domain with DOMAIN for the ML")
    print(" -f LIST[,LIST] => only process mailing list LIST(s) (matching happens after applying -p/-r/-t)")
    print(" -x LIST[,LIST] => do not process mailing list LIST(s) (matching happens after applying -p/-r/-t)")
    print(" -u FILE        => use user  list from file (ldif) instead of calling udm")
    print(" -g FILE        => use group list from file (ldif) instead of calling udm")
    print(" -s user        => switch ID (uid and gid) to to user (name) for mm3 config, default list")
    sys.exit(ret)

def main(argv):
    global debug, testMode, testMode2, noDelete, admin, prefix, userFile, groupFile
    global filterList, excludeList, replaceList, nested
    global userManager
    translate = None
    identity = "list"
    # TODO: Use getopt
    try:
        (optlist, args) = getopt.gnu_getopt(argv[1:], 'hdnNka:t:f:x:p:u:g:s:r:R:')
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
        if opt == "-R":
            nested = int(arg)
            continue
        if opt == "-k":
            noDelete = True
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
            filterList = arg.split(",")
            continue
        if opt == "-x":
            excludeList = arg.split(",")
            continue
        if opt == "-r":
            replaceList.append(arg.split(","))
            continue
        if opt == "-u":
            userFile = arg
            continue
        if opt == "-g":
            groupFile = arg
            continue
        if opt == "-s":
            identity = arg
            continue

    lUsers = collectUsers()
    lGroups = collectGroups(lUsers, translate)
    if nested:
        recurseNestedGroups(lUsers, lGroups, nested)
    # Debugging: Dump info
    for lg in lGroups:
        assert(lg.mailAddr is not None)
        if debug:
            print("LDAP(%s): %s" % (lg.cn, lg.mailAddr))
        for lu in lg.userList:
            if debug:
                print(" %s: %s %s" % (lu.dName, lu.primMail, lu.mails))
        if debug:
            print()

    if identity:
        # Switch to mailman user ID
        pwid = pwd.getpwnam(identity)
        #print("Switching identity to %s: %i:%i" % (identity, pwid.pw_uid, pwid.pw_gid))
        os.setegid(pwid.pw_gid)
        os.seteuid(pwid.pw_uid)
        assert(os.geteuid() == pwid.pw_uid)

    # Read existing MLs and determine needed changes
    initialize()
    userManager = getUtility(IUserManager)
    mLists = collectMMLists()
    if debug:
        for ml in mLists:
            print(ml)

    reconcile(lGroups, mLists)

    with ExitStack() as resources:
        # If given a bogus subcommand, the database won't have been
        # initialized so there's no transaction to commit.
        if config.db is not None:
            resources.enter_context(transaction())

    return 0


if __name__ == "__main__":
    main(sys.argv)
