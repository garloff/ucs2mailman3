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
    def __init__(self):
        self.cn = None
        self.mailAddr = None
        self.userList = []
    def __init__(self, lines):
        self.cn = ldapAttr(lines[0], "cn")[0]
        self.mailAddr = ldapParse(lines, "mailAddress")[0]
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

