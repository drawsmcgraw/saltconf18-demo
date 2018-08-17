#!/usr/bin/python

############################################################################################
# This script performs a rolling action on Salted machines.                                #
#                                                                                          #
# As this script uses Salt's API and our Salt states it must be run on the Salt Master.    #
############################################################################################

import time
import logging
import os
import datetime
import requests
import json
import sys
import traceback
import argparse

from getpass import getpass
from argparse import RawTextHelpFormatter

import salt.client
import salt.client.ssh.client
import salt.utils

# Track how long we take.
start_time = time.time()


# Argument Parsing:
parser = argparse.ArgumentParser(description='Perform rolling restarts/upgrades/reboots of clusters. Must be run on the Salt Master.',
                                 formatter_class=RawTextHelpFormatter)
parser.add_argument('-n',
                    '--minions',
                    dest='minions',
                    action='store',
                    help='Comma-separated list. Minions to act upon.\n'
                         'Example: minion-01.example.local,minion-02.example.local')
parser.add_argument('-e',
                    '--exclude',
                    dest='exclude',
                    action='store',
                    help='Comma-separated list. Exclude hosts from rolling restart.\n'
                         'Example: bad-data-node-09,bad-data-node-55')
parser.add_argument('-t',
                    '--test',
                    dest='test',
                    action='store_true',
                    help='Displays hosts that script would be run on, does not perform any action.')
parser.add_argument('-s',
                    '--ssh',
                    dest='use_ssh',
                    action='store_true',
                    default=False,
                    help='Use Salt-SSH. This requires a current, functioning Salt-SSH deployment.')
parser.add_argument('-l',
                    '--log-level',
                    dest='log_level',
                    action='store',
                    default='INFO',
                    help='Log level.\n'
                         'Examples include \'info\', \'warn\', and \'debug\'. Defaults to INFO')
parser.add_argument('-r',
                    '--reboot',
                    dest='reboot',
                    action='store_true',
                    default=False,
                    help='Reboot after system upgrade (only applies to the \'update_system\' action).')
parser.add_argument('-a',
                    '--action',
                    required=True,
                    dest='action',
                    action='store',
                    default='upgrade',
                    choices=['update_configs',
                             'reboot_host',
                             'update_system'],
                    help='Action to perform.\n'
                         'update_configs = Update the configuration for our service.\n'
                         'reboot_host    = Reboot the machine.\n'
                         'update_system  = Update all packages on a system (i.e. \'yum update\').\n'
                         '                Use with \'-r\' to reboot when finished upgrading.')

options = parser.parse_args()

# Did the user specify a log level?
# Note: Not using `importlib` because we need this to work on Python 2.6.
options_log_level = options.log_level.upper()
if not options_log_level == 'INFO':
    try:
      log_level = getattr( __import__('logging', fromlist=[options_log_level]),
                          options_log_level)
    except AttributeError:
      print "Log level '{0}' is not available. Please try again or run without specifying log level (the default will be INFO).".format(options_log_level)
      exit(1)
else:
    log_level = options_log_level

# Set a format which is simpler for console use
formatter = logging.Formatter('%(asctime)s %(levelname)-8s %(message)s')
fh = logging.FileHandler('cluster_upgrade-' + time.strftime('%Y_%m_%d_%H_%M' + '.log'))
fh.setFormatter(formatter)
fh.setLevel(log_level)

# Set up logging to console
console = logging.StreamHandler()
console.setLevel(log_level)
console.setFormatter(formatter)

# Add the handlers to the root logger
logging.getLogger('').addHandler(fh)
logging.getLogger('').addHandler(console)
logger = logging.getLogger(__name__)

# Turn down the verbosity from some modules we use to avoid polluting the output.
logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("salt").setLevel(logging.WARNING)

# We use the Salt Client API instead of making 'salt' calls on the cmd line.
# Salt-ssh stores return data in the 'return' key.
# Minion-based runs store return data in the 'ret' key.
if options.use_ssh:
    # local = salt.client.ssh.client.SSHClient(c_path='/etc/salt/master')
    SALT_RETURN_KEY = 'return'
else:
    # local = salt.client.LocalClient()
    SALT_RETURN_KEY = 'ret'

# Use the native Salt utils for checking if Salt runs succeeded
state_checker = salt.utils.check_state_result

#############
# Functions #
#############

def salt_client(use_ssh=False):
    if use_ssh:
        return salt.client.ssh.client.SSHClient(c_path='/etc/salt/master')
    else:
        return salt.client.LocalClient()

def do_request(verb, server, auth, verify=False, data=None, ignore_errors=False):
    attempts = 30
    while attempts > 0:
        try:
            r = requests.request(verb.upper(), server, auth=auth, verify=verify, data=data)

            # Handle HTTP 400 errors, i.e. wrong password.
            if str(r.status_code).startswith('4') and not ignore_errors:
                logging.error("Request to '{0}' returned HTTP {1}. Are your credentials correct?".format(server, r.status_code))
                exit(1)

            return r
        except requests.exceptions.ConnectionError:
            attempts -= 1
            logging.warn("Attempted connection to {0} failed. Trying {1} more times".format(server, attempts))
            time.sleep(5)

def do_salt_call(target, function, use_ssh=False, **kwargs):
    """
    Handle executing Salt calls and catching failed calls.
    This method is for Salt calls whose job data includes the 'ret' or 'return' keyword.
    This method should NOT be used for Salt calls that return simple or boolean data,
      i.e. {'minion-01': True}  or
           {'minion-01': '4'}


    This method only handles Salt calls with a single target.
    DO NOT use this method for calls with multiple targets.

    :param target:
    :param function:
    :param kwargs:
    :return:
    """
    client = salt_client(use_ssh)
    if kwargs.has_key('fun_args'):
        ret = client.cmd(target, function, [fun_args], full_return=True)
    else:
        ret = client.cmd(target, function, full_return=True)

    if not ret[target][SALT_RETURN_KEY]:
        logging.error("The Salt call failed to return any data. Following is the entire payload for inspection.")
        logging.error(ret)
        raise Exception

    return ret



def check_salt_run_status(ret_data):
    """
    Confirm that a Salt Run was was a success.
    The return data is a dict, where each key is the minion name.
    In each key is a dict, where each key is the state that was run.
    That state has a value, 'result', that says whether or not the state ran successfully.
    I.E.
     {'minion-01': {
     	'ensure_es_installed': { 'result': True}
     	'ensure_es_running': { 'result': False}
     	}
    { 'minion-02': {...
          }
     }
    If the Salt run failed to compile, then instead of a set of values,
    the only value under the minion is a list with a single entry,
    which is the Salt error.
    """

    logging.debug("Checking return data {0}".format(ret_data))

    minions = ret_data.keys()

    # Do nothing if it was a success
    state_succeeded = True
    for minion in minions:
        if not state_checker(ret_data[minion][SALT_RETURN_KEY]):
            state_succeeded = False
    if state_succeeded:
        return True

    # If we get here, we need to find out what went wrong.
    for minion in minions:

        # Did the Salt run fail to compile?
        if type(ret_data[minion][SALT_RETURN_KEY]) is list:
            logging.error("Salt run failed. Full Salt run output follows.")
            logging.error("{0}".format(ret_data))
            return False
        else:
            # Salt run successfully finished but at least one state failed.
            # See which one failed.
            for state, values in ret_data[minion][SALT_RETURN_KEY].items():
                if values.get(SALT_RETURN_KEY, None) is not True:
                    logging.error("A Salt state on minion '{0}' failed with a status of '{1}'.".format(minion, values.get('result')))
                    logging.error("Comment from failed state is: {0}".format(values.get('comment')))
                    logging.error("The entire return value is: {0}".format(ret_data))
                    return False

    # Salt thinks something went wrong with the state run but we couldn't find it. Continue anyway, but dump
    # the return data so users can conduct some research.
    logger.warn("Salt state checker reported a failed run but we could not find the error.")
    logger.warn("We will continue with the process. Please see the full return data below for more details.")
    logger.warn(ret_data)
    return True

def update_configs(node):
    """
    Update configurations for our service via Salt states. 
    Do not restart the service just yet.
    """
    client = salt_client(options.use_ssh)
    logging.info("Updating configuration files on {0}".format(node))
    pillar = {'foo': 'bar'}
    output = client.cmd(node, 
                        'state.sls',
                        ['haproxy.update_configs'],
                        kwarg={ 'pillar': pillar },
                        full_return=True)

    if not check_salt_run_status(output):
        raise Exception("Salt run failed. Salt run output follows:\n{0}".format(output))

    return


def restart_service(node):
    """
    Attempt to restart our service.
    """
    tries = 3
    success = False
    client = salt_client(options.use_ssh)

    while not success:
        logging.info("Restarting haproxy on node '{0}'".format(node))
        output = client.cmd(node, 'service.restart', ["haproxy"])

        # Wait a moment, then check the status.
        time.sleep(2)
        output = client.cmd(node, 'service.status', ["haproxy"])

        # Did the service fail to start?
        # Return data is just {'minion_name': Bool}
        if not output[node]:
            tries -= 1
            if tries == 0:
                raise Exception
            logging.warn("Failed to restart haproxy on {0}. Number of retries left: {1}".format(node, tries))
            time.sleep(5)
        else:
            success = True

def update_system(node):
    """
    Upgrade all packages on the system.
    i.e. perform a 'yum update'
    :param node:
    :return:
    """

    client = salt_client(options.use_ssh)
    logging.info("Updating all system packages on '{0}'.".format(node))
    output = client.cmd(node, 'pkg.upgrade', ['refresh="true"'], full_return=True)
    if not check_salt_run_status(output):
        raise Exception

    return

def ping_all_nodes(nodes):
    """
    Execute a test.ping() on all Salt minions.
    """
    client = salt_client(options.use_ssh)

    ret = client.cmd(nodes, 'test.ping', tgt_type='list', full_return=True)

    # Did any fail?
    failed_minions = []
    for minion,value in ret.items():
        if value == False:
            failed_minions.append(minion)

    if len(failed_minions) > 0:
        return failed_minions
    else:
        return True

def restart_host(node):
    """
    Perform a host restart, usually to finish installation of new kernels.
    Does not return until the host has completed rebooting and is responding to pings.
    :param node:
    :return:
    """

    timeout = 300 # 5 minutes
    period  = 10  # 10 seconds between checks
    client = salt_client(options.use_ssh)

    # Restart the machine.
    logging.info("Restarting host '{0}'.".format(node))

    # Grab the uptime before rebooting. We'll use this to confirm that the machine has
    # actually rebooted.
    uptime_before_reboot = do_salt_call(node, 'status.uptime', use_ssh=options.use_ssh)[node][SALT_RETURN_KEY]['seconds']
    output = client.cmd(node, 'system.reboot', full_return=True)

    # Periodically ping the host, up to <timeout> seconds, before giving up.
    while timeout > 0:
        logging.info("Pinging host '{0}' for connectivity.".format(node))
        output = client.cmd(node, 'status.uptime', full_return=True)

        # Machine is not responding (i.e. likely booting up).
        # Tick down the timer and poll again.
        # Need to account for ssh-based returns and minion-based returns.
        if (options.use_ssh and output[node].get('retcode') != 0) or \
                (output[node] is False):
            logging.info("No response from host '{0}'. {1} seconds before giving up.".format(node, timeout))
            timeout -= period
            time.sleep(period)

        # Check the uptime again. If it's larger than our uptime_before_reboot, then
        # the machine hasn't actually rebooted yet.
        #
        # The return data looks like this upon success:
        # {'es-dev-03.example.local': {'users': 1,
        #                              'seconds': 1200,
        #                              'since_t': 1513615153,
        #                              'days': 0,
        #                              'since_iso': '2017-12-18T16:39:13.749796',
        #                              'time': '0:20'}
        # }
        else:
            current_uptime = output[node][SALT_RETURN_KEY]['seconds']
            if (current_uptime < uptime_before_reboot):
                logging.info("Host '{0}' has completed rebooting".format(node))
                return

            # Host hasn't actually rebooted yet.
            else:
                logging.info("Waiting for host '{0}' to begin rebooting".format(node))
                time.sleep(period)

    # If we get here, then we timed out waiting for the host to come back.
    logging.error("Timed out waiting for host '{0}' to respond to ping. Failing.".format(node))
    raise Exception


#########################
# Begin Rolling Actions #
#########################

def main():

    # Need root permissions
    if not os.getuid() == 0:
      print "This script needs root permissions to execute."
      exit(1)

    minions = options.minions.split(',')

    # Are we just testing?
    if options.test:
        logging.info("Targeting the following minions:")
        for minion in minions:
            logging.info("  {0}".format(minion))
        exit(0)

    # All Salt minions need to respond to pings, else the task will fail.
    logging.info("Pinging all nodes to confirm connectivity")
    all_nodes_up = ping_all_nodes(minions)
    if not all_nodes_up == True:
        logging.error("Not all nodes responded to pings. Halting execution. Following are the nodes that did not respond.")
        for node in all_nodes_up:
            logging.error('- {0}'.format(node))
        logging.error("Please fix the issue or exclude the problem nodes.")
        exit(1)
    logging.info("All minions responded to ping. Continuing.")

    # For each minion...
    for minion in minions:
        logging.info("Beginning work on {0}".format(minion))
        tries = 3
        success = False
        while not success:
            try:

                # Update the configuration files.
                if options.action == 'update_configs':
                    try:
                        update_configs(minion)
                        restart_service(minion)
                    except Exception as e:
                        logging.error("Failed to update and restart haproxy on minion {0}. Exiting".format(minion))
                        exit(1)

                # Just restart the service.
                elif options.action == 'restart_service':
                    restart_service(minion)

                # Reboot the machine.
                elif options.action == 'reboot_host':
                    restart_host(minion)
                    wait_for_es_service(minion)

                # Upgrade all packages on the system and (optionally) reboot it.
                elif options.action == 'update_system':
                    update_system(minion)
                    if options.reboot:
                        restart_host(minion)
                    wait_for_es_service(minion)

                success = True

            except Exception as e:
                tries -= 1
                logging.warn("Work on minion '{0}' failed. Number of retries left: {1}. Exception text follows:\n{2}".format(minion, tries,traceback.format_exc()))
                if options.use_ssh:
                    logging.warn("Wiping salt-ssh shim before next retry")
                    client = salt_client(options.use_ssh)
                    client.cmd(minion, 'test.ping', ssh_wipe=True, full_return=True)
                if tries == 0:
                    logging.error("Failed on minion'{0}'. Exiting now.".format(minion))
                    exit(1)

    runtime = round(time.time() - start_time, 0)
    logging.info("Finished. Total time in hh:mm:ss is {0}".format(str(datetime.timedelta(seconds=runtime))))


if __name__ == "__main__":
    main()

