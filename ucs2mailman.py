#!/usr/bin/env python3
#
# Feed mailman3 MLs with group members from UCS LDAP
#
# (c) garloff@osb-alliance.com, 9/2021
# SPDX-License-Identifier: AGPL-3
#

import os, sys, subprocess, re
from operator import methodcaller, attrgetter
import bisect

#mailmanBin = "/usr/lib/mailman3/bin/mailman"
udmBin = "/usr/sbin/udm"

debug = False

from mailman.config import config
from mailman.core.i18n import _
from mailman.core.initialize import initialize
from mailman.database.transaction import transaction
from mailman.interfaces.command import ICLISubCommand
from mailman.utilities.modules import add_components
from mailman.version import MAILMAN_VERSION_FULL
from public import public

from mailman.interfaces.domain import IDomainManager
#from mailman.interfaces.domain import IMailingList
from mailman.interfaces.member import MemberRole
from zope.component import getUtility


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
            self.mailAddr = mailAddr[0]
        users = ldapParse(lines, "users")
        self.userList = []
        if users:
            for ln in users:
                self.userList.append(ldapAttr(ln, "uid")[0] + "@" + str.join(".", ldapAttr(ln, "dc")))


class ldapUser:
    def __init__(self, lines):
        self.uid = ldapAttr(lines[0], "uid")[0]
        self.primMail = self.uid + "@" + str.join(".", ldapAttr(lines[0], "dc"))
        self.dname = ldapParse(lines, "displayName")
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
            self.mlMembers.append(member.address.email)
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
        return 1
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
        return 1
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
manager = None

def collectMMLists():
    "Get all mailings lists from Mailman3"
    global manager
    initialize()
    manager = getUtility(IDomainManager)
    lists = []
    for domain in manager:
        for ml in domain.mailing_lists:
            lists.append(mList(ml))
    return lists

def allMails(lUsers, userMail):
    "return list of extra mails from user"
    user = findUser(lUsers, userMail)
    if user:
        return user.mails
    else:
        return []

def reconcile(lGroups, lUsers, mLists):
    "Reconcile Mailman3 lists with input from LDAP"
    # Now: Reconciliation steps
    mListDict = { x.mlName: x for x in mLists }
    for lg in lGroups:
        # (1) Create new lists from LDAP Groups
        if lg.mailAddr not in mListDict:
            print("Mailing list %s missing" % lg.mailAddr)
            #  (1a) Create ML with useful defaults
            #  (1b) Add members
            #  (1c) Add nonMembers
        else:
            # (2) For existing lists:
            ml = mListDict(lg.mailAddr)
            for luser in lg.userList:
                #  (2a) Any subscribers (members) missing?
                if luser not in ml.mlMembers:
                    print("Subscriber %s to list %s missing" % (luser, lg.mailAddr))
                #  (2b) Any nonMembers (whitelisted posters) missing?
                for addtlMail in allMails(lUsers, luser):
                    if addtlMail not in ml.mlNonMembers:
                        print("Whitelist entry %s (user %s) to list %s missing" % (addtlMail, luser, lg.mailAddr))
            #  (2c) Any extra subscribers (members) that should be removed?
            for member in ml.mlMembers:
                if member not in lg.userList:
                    print("Subscriber %s should be removed from list %s" % (member, lg.mailAddr))
            # Note: Extra nonMembers are OK
    # Note: Extra lists are OK
    pass

def main(argv):
    global debug
    translate = None
    if len(argv) > 1 and argv[1] == "-d":
        debug = True
        argv = argv[1:]
    if len(argv) > 2 and argv[1] == "-t":
        translate = argv[2]
        argv = argv[2:]

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

    # Read existing MLs and determine needed changes
    mLists = collectMMLists()
    if debug:
        for ml in mLists:
            print(ml)

    reconcile(lGroups, lUsers, mLists)

    return 0



if __name__ == "__main__":
    main(sys.argv)
