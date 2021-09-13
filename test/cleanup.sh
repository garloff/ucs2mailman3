#!/bin/bash
echo "Cleaning up ..."
rm out??
mailman remove test-team1@test.domain || exiterr 98 "Could not remove test-team1 list"
mailman remove test-team2@test.domain || exiterr 98 "Could not remove test-team2 list"
test/deluser.py first1.last1@test.domain first2.last2@test.domain first3.last3@test.domain first4.last4@test.domain
