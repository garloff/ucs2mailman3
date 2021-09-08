#!/usr/bin/env python3
#
# Feed mailman3 MLs with group members from UCS LDAP
#
# (c) garloff@osb-alliance.com, 9/2021
# SPDX-License-Identifier: AGPL-3
#

import os, sys, subprocess, re
#from operator import methodcaller, attrgetter
import bisect

mailmanBin = "/usr/lib/mailman3/bin/mailman"
udmBin = "/usr/sbin/udm"

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
    def __init__(self):
        self.mlName = None
        self.mlMembers = []


def collectGroups():
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
    return filter(lambda x: x.mailAddr is not None, groups)

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

def main(argv):
    lGroups = collectGroups()
    lUsers = collectUsers()
    # Debugging: Dump info
    for lg in lGroups:
        assert(lg.mailAddr is not None)
        print("LDAP(%s): %s" % (lg.cn, lg.mailAddr))
        for us in lg.userList:
            uMails = []
            user = findUser(lUsers, us)
            if user:
                uMails = user.mails
            print(" %s: %s" % (us, uMails))
        print()

    # Read existing MLs and determine needed changes
    return 0


if __name__ == "__main__":
    main(sys.argv)
