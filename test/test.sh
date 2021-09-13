#!/bin/bash
# Ensure you have list privs
#
# exiterr
exiterr()
{
  ERR="$1"
  shift
  echo "FAIL: $*" 1>&2
  exit $ERR
}

TT1="test-team1@test.domain"
TT2="test-team2@test.domain"
TT="$TT1 $TT2"

test_memberships()
{
	echo "Test membership"
	mailman members -R member $TT1 > out1m || exiterr $1 "mailman returning error"
	mailman members -R nonmember $TT1 > out1n || exiterr $1 "mailman returning error"
	mailman members -R member $TT2 > out2m || exiterr $1 "mailman returning error"
	mailman members -R nonmember $TT2 > out2n || exiterr $1 "mailman returning error"
	diff -u out1m test/$3_members1 || exiterr $2 "wrong members list1"
	diff -u out1n test/$3_nonmembers1 || exiterr $2 "wrong nonmembers list1"
	diff -u out2m test/$3_members2 || exiterr $2 "wrong members list2"
	diff -u out2n test/$3_nonmembers2 || exiterr $2 "wrong nonmembers list2"
}

LISTS=$(mailman lists | grep test.domain)
echo "Test clean state"
test -z "$LISTS" || exiterr 1 "test domain lists already existing"
echo "Create lists 01"
./ucs2mailman.py -a scs@garloff.de -u test/01_user.ldif -g test/01_group.ldif || exiterr 2 "ucs2mailman returning non-0"
echo "Test list existence"
LISTS=$(mailman lists | grep test.domain)
LISTS=$(echo $LISTS)
if test "$LISTS" != "$TT"; then exiterr 3 "Lists not created"; fi
test_memberships 4 5 01
echo "Rerun create lists 01"
./ucs2mailman.py -a scs@garloff.de -u test/01_user.ldif -g test/01_group.ldif || exiterr 6 "ucs2mailman returning non-0"
echo "Test list existence"
LISTS=$(mailman lists | grep test.domain)
LISTS=$(echo $LISTS)
if test "$LISTS" != "$TT"; then exiterr 7 "Lists not created"; fi
test_memberships 8 9 01





# cleanup
rm out??
mailman remove test-team1@test.domain || exiterr 98 "Could not remove test-team1 list"
mailman remove test-team2@test.domain || exiterr 98 "Could not remove test-team2 list"
