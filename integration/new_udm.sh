#!/bin/bash
#
# Cron job for root to check whether univention user database has changed
# If so, create new dumps in /var/list/
# and call update_mls.sh there.
# (c) Kurt Garloff <garloff@osb-alliance.com>, 9/2021
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Better option: https://docs.software-univention.de/developer-reference-5.0.html#chap:listener
#
# Test whether the LDAP database has changed and create new dumps if needed
if test /var/lib/univention-ldap/ldap/data.mdb -nt /var/list/udm.users; then
	/usr/sbin/udm users/user list | grep -v '^  jpegPhoto:' > /var/list/udm.users
	/usr/sbin/udm groups/group list > /var/list/udm.groups
	chown list:list /var/list/udm.*
	chmod o-r /var/list/udm.*
	sudo -u list /var/list/update_mls.sh
fi
