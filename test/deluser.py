#!/usr/bin/env python3

import sys, os, pwd

from zope.component import getUtility
from mailman.interfaces.usermanager import IUserManager
from contextlib import ExitStack
from mailman.core.initialize import initialize
from mailman.database.transaction import transaction
from mailman.config import config

pw = pwd.getpwnam("list")
os.setegid(pw.pw_gid)
os.seteuid(pw.pw_uid)

initialize()

userManager = getUtility(IUserManager)

for user in sys.argv[1:]:
    mUser = userManager.get_user(user)
    if mUser:
        print("Deleting user %s" % mUser)
        userManager.delete_user(mUser)


with ExitStack() as resources:
    # If given a bogus subcommand, the database won't have been
    # initialized so there's no transaction to commit.
    if config.db is not None:
        resources.enter_context(transaction())


