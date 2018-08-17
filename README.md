# Salt Python API Demo
This code demonstrates the ability to take a rolling action (i.e. "rolling upgrade" or "rolling restart") on a nontrivial-sized cluster. 

For this demonstration, we target a fleet of haproxy servers but this can be changed out for any technology (Kafka, Hadoop, Elasticsearch, in-house app farm, etc).

Use cases considered:
1) Rolling Update of configurations, then restart, haproxy servers.
2) Same as #1, but detect the failure of haproxy to start, then halt execution ("do no harm").
3) Rolling reboot (i.e. in the need of a kernel update).

Help output:
```
usage: salt_api_demo.py [-h] [-n MINIONS] [-e EXCLUDE] [-t] [-s]
                        [-l LOG_LEVEL] [-r] -a
                        {update_configs,reboot_host,update_system}

Perform rolling restarts/upgrades/reboots of clusters. Must be run on the Salt Master.

optional arguments:
  -h, --help            show this help message and exit
  -n MINIONS, --minions MINIONS
                        Comma-separated list. Minions to act upon.
                        Example: minion-01.example.local,minion-02.example.local
  -e EXCLUDE, --exclude EXCLUDE
                        Comma-separated list. Exclude hosts from rolling restart.
                        Example: bad-data-node-09,bad-data-node-55
  -t, --test            Displays hosts that script would be run on, does not perform any action.
  -s, --ssh             Use Salt-SSH. This requires a current, functioning Salt-SSH deployment.
  -l LOG_LEVEL, --log-level LOG_LEVEL
                        Log level.
                        Examples include 'info', 'warn', and 'debug'. Defaults to INFO
  -r, --reboot          Reboot after system upgrade (only applies to the 'update_system' action).
  -a {update_configs,reboot_host,update_system}, --action {update_configs,reboot_host,update_system}
                        Action to perform.
                        update_configs = Update the configuration for our service.
                        reboot_host    = Reboot the machine.
                        update_system  = Update all packages on a system (i.e. 'yum update').
                                        Use with '-r' to reboot when finished upgrading.
```
