# Server Upgrade Notes

## Things to Do Before

* Pull the WAN ethernet out.
* Run an rsync backup.
* Swap in the new boot drive.

## Things to Restore After

* Copy my Bash config over.
* Copy my Bash history over.
* Copy my SSH stuff over.
* Do the same for `root` and `localuser`.
* Stop Apache. Restore Apache logs. Restart Apache.
* Stop arr_suite unit and containers. Restore `/opt/arr_suite`. Restart arr_suite.
* Stop Kerberos server. Restore the KDC dump (from `/root`). Restore keytab. Restore the stash file. Restart KDC.
* Stop Jellyfin. Restore its config files. Restart it.
* Stop all the mail units. Import the ZFS pool. Restart mail.
* Stop Postgres. Restore all the DBs. Restart Postgres.
* Re-run the prod deploy of bookworm. Stop the unit. Restore the files. Restart it.
* Stop tarsnap. Restore the files. Restart it.

## Things to Work On Eventually

* Run `sudo time ./estimate-dedup.sh` overnight.
* Re-run bookworm performance tests.
* Can Jellyfin use hardware transcoding? Is it already?
* Is there a paid YTDL service?
* Can we also install Plex? Is it any better?
* Move backups to external drive, perhaps as an offline zpool with dedup.
* Switch to nushell?
* Setup old desktop or server chassis as remote ZFS backup box?
* Ensure that command lines and GHA jobs ding loudly when done.

## Completely Unrelated Thoughts

* Need to buy some office art.
    * Smokey the Bear
* Need to print out some photos for office.
* Need to email Kevin, for Tuesday.
