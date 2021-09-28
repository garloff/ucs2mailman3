#!/bin/bash
#
# Job to be run with mailman identity in /var/list
# This will read the UCS directory dumps and update the
# mailing list subscriptions.
#
# Replace ADMIN@DOMAIN with an existing admin user
#  and MLDOMAIN with the domain for the mailing lists
#  (or drop the -t parameter to go with the domain from LDAP).
#
# (c) Kurt Garloff <garloff@osb-alliance.com>, 9/2021
# SPDX-License-Identifier: AGPL-3.0-or-later
#

cd /var/list
if test udm.groups -nt stamp; then
	ucs2mailman.py -a ADMIN@DOMAIN -t MLDOMAIN -u udm.users -g udm.groups && touch stamp
fi


