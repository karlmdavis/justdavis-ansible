# ZFS Setup

This role configures the base/platform parts required for the system to host its ZFS pool.

It very intentionally does not attempt to create or import the pool in production,
  as that's a very stateful operation with the risk of data loss if you get it wrong.

## Importing the ssd_pool

If you're migrating to a new server or OS install,
  you'll want to import the existing ZFS drive(s)
  to preserve their contents
  (without having to mess with a backup and restore operation).
After applying this role,
  run the following commands to import the pool to the new system:

```bash
# ZFS should auto-scan /dev/ to find the pools available for import.
$ sudo zpool import -f ssd_pool
$ sudo zfs load-key ssd_pool
```

Before proceeding further,
  stop and backup the services that you're about to swap data in for:

```bash
$ sudo systemctl stop {postgresql,postfix,samba}
$ sudo mkdir /root/zfs-mount-backups
$ sudo cp -a \
  /var/lib/postgresql \
  /var/vmail \
  /var/fileshares \
  /root/zfs-mount-backups/
```

Then, you should be safe to mount the ZFS datasets:

```bash
$ sudo zfs mount -a
```

And then restart the services:

```bash
$ sudo systemctl start {postgresql,postfix,samba}
```

Note that the `zfs mount` command will shadow
  the pre-existing directories for the mount points.
If service versions are getting upgraded as part of this,
  you may have a tricky time getting things started back up after the `zfs mount`.
(Particularly for PostgreSQL,
  where major version upgrades require a dump-and-restore approach.)
Plan ahead!
