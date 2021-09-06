#!/usr/bin/env python3
#
# Feed mailman3 MLs with group members from UCS LDAP
#
# (c) garloff@osb-alliance.com, 9/2021
# SPDX-License-Identifier: AGPL-3
#

import os, sys, subprocess, re

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
    def __init__(self):
        self.uid = None
        self.dName = None
        self.primMail = None
        self.mails = []
        self.groups = []

class mList:
    def __init__(self):
        self.mlName = None
        self.mlMembers = []

def main(argv):
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
    for lg in groups:
        if lg.mailAddr is not None:
            print("LDAP(%s): %s\n %s" % (lg.cn, lg.mailAddr, lg.userList))
    # TODO:
    # Read LDAP users for mail aliases (whiteliste)
    # Read existing MLs and determine needed changes
    return 0


if __name__ == "__main__":
    main(sys.argv)
