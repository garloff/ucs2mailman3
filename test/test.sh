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

LISTS=$(mailman lists | grep test.domain)
test -z "$LISTS" || exiterr 1 "test domain lists already existing"
./ucs2mailman.py -a scs@garloff.de -u test/01_user.ldif -g test/01_group.ldif || exiterr 2 "ucs2mailman returning non-0"
LISTS=$(mailman lists | grep test.domain)
LISTS=$(echo $LISTS)
if test "$LISTS" != "$TT"; then exiterr 3 "Lists not created"; fi
mailman members -R member $TT1 > out1m || exiterr 4 "mailman returning error"
mailman members -R nonmember $TT1 > out1n || exiterr 4 "mailman returning error"
mailman members -R member $TT2 > out2m || exiterr 4 "mailman returning error"
mailman members -R nonmember $TT2 > out2n || exiterr 4 "mailman returning error"
diff -u out1m test/01_members1 || exiterr 5 "wrong members list1"
diff -u out1n test/01_nonmembers1 || exiterr 5 "wrong nonmembers list1"
diff -u out2m test/01_members2 || exiterr 5 "wrong members list2"
diff -u out2n test/01_nonmembers2 || exiterr 5 "wrong nonmembers list2"




# cleanup
rm out??
mailman remove test-team1@test.domain || exiterr 98 "Could not remove test-team1 list"
mailman remove test-team2@test.domain || exiterr 98 "Could not remove test-team2 list"
