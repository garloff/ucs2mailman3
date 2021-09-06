# ucs2mailman.py

Script to read LDAP directory on UCS server and manage
appropriate mailman3 mailing lists.

LDAP groups with mailAddress: that is not None will have
should have mailman lists with the users subscribed.

Purpose of the script is to sync from UCS to mailman:
* Create missing lists
* Add missing users
* Remove extra users
* Also create whitelist for other mail addresses
  of subscribers. These will not typically cleaned
  up as we don't know, whether these have been added
  by the admin and should stay ...


