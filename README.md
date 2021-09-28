# ucs2mailman.py

Script to read LDAP directory on a UCS server and manage
mailman3 mailing lists corresponding to the LDAP groups.

The straight forward use of this script is translating LDAP groups
with mailAddress: (which is not None) to mailman lists with that
name and all group members as subscribers. The subscription will
happen with the main identity (uid @ domain), and all the other
known user mail addresses from the LDAP users will be added as
non-members which can post without being moderated.

As UCS does mail forwarding from the groups already (without
offering a full feature set for mailing lists), it is a good
idea to leave the normal UCS redistribution in place and create
mailing lists with different names. Using a secondary domain
(option ``-t``), adding a prefix (``-p``) or using arbitrary
name replacements (``-r``) can be used to implement this.

It's also possible to only handle a subset of the LDAP groups
by using filters (options ``-f`` and ``-x``). Lists can also
be set to only ever have group members added but never removed
(``-k``). Without this option, subscribers from the managed
lists that are not (no longer) part of the respective LDAP
group will be removed from the list.

ucs2mailman.py will only do changes to the lists that have
mailAddress: set in the UCS group directory (after applying
the translations from option ``-p``, ``-r``, ``-t`` -- in
this order) and the filters (``-f``, ``-x``), so manually
managed lists, partially managed lists (``-k``) and fully
automatically managed lists can coexist on the same mailman3
instance.

Note that the changes to the mailman3 config are performed
with the user identity of mailman3 owner, ``list`` on Debian
(UCS) systems. The script will change its euid/egid to this
identity (unless overriden by ``-s``), so it needs to be started
with this identity already or as root. (``-s ""`` would skip
the switching, but you'll likely not be able to talk to mailman3
this way.)

## Installation and usage

You need to have mailman3 installed and set up and an admin
user configured which you pass with option ``-a`` as owner/
admin for any newly created lists.

By default, the script calls ``udm`` to get UCS LDAP users
and group lists -- note that this typically requires root privileges.
You can instead use the options ``-u`` and ``-g`` to read the
directory from files. (It is advisable to strip hashed passwords
and jpegPhotos from dumps that end up on your disk.)

On UCS hosts, your postfix configuration is automatically generated
by the univention scripts. To have mailman3 work, you need to tweak
the config file creation logic. Apply the patch from
``integration/postfix-mailman.diff``.

Next step is to monitor the user/group database and ensure that changes
are automatically reflected in mailman subscriptions. On UCS, this can be
done by calling ``integration/new_udm.sh`` every few minutes from a cron
job as root. The script will then call ``/var/list/update_mls.sh`` as user
``list`` *if* the user/group databases have changed. Copy 
``integration/update_mls.sh`` there (and make sure ``new_udm.sh`` and
``update_mls.sh`` are executable by the respective users).

## Testing

You can use the options ``-d`` and ``-n`` to test and understand
what changes ``ucs2mailman.py`` would to your mailings lists 
prior to having it performing changes.

There is a test script which tests creation of mailing lists, adding
users and changing and removing users. See ``test/`` directory.

## TODO

The LDAP (LDIF) parsing does understand the formatting from
``udm`` output as well as plain ``ldapsearch`` output (if you switch
off line wrapping). However, ``ucs2mailman.py`` has really been
developed for being run on a UCS instance, so expect a few tweaks
to be required on non-UCS LDAP hosts for it to really be useful.
Patches (pull requests) are welcome!

mailman3 has a REST interface -- from hindsight, it might have been
cleaner to use it to create mailing lists and handle subscription
management. This would have avoided to change the identity to
the mailman3 user. I have not investigated whether the REST interface
exposes all needed controls and is straight forward to talk to, so this
may or may not be a practical approach.

The ``test/`` directory would likely benefit from more test cases.

UCS offers to run scripts via "listener plugins" after changes to the
LDAP directory, see
https://docs.software-univention.de/developer-reference-5.0.html#chap:listener
We could filter group changes and implement a ``postrun`` action. This
would seem cleaner than looking at the database file every few minutes
from a cron job.

