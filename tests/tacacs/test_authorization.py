import crypt
import paramiko
import pytest

from .test_ro_user import ssh_remote_run
from .utils import stop_tacacs_server, start_tacacs_server
from tests.common.helpers.assertions import pytest_assert
from tests.common.utilities import skip_release

pytestmark = [
    pytest.mark.disable_loganalyzer,
    pytest.mark.topology('any'),
    pytest.mark.device_type('vs')
]

logger = logging.getLogger(__name__)

TIMEOUT_LIMIT   = 120

def ssh_connect_remote(remote_ip, remote_username, remote_password):
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(remote_ip, username=remote_username, password=remote_password, allow_agent=False, look_for_keys=False, auth_timeout=TIMEOUT_LIMIT)
    return ssh

def check_ssh_connect_remote_failed(remote_ip, remote_username, remote_password):
    login_failed = False
    try:
        ssh_client_local = ssh_connect_remote(remote_ip, remote_username, remote_password)
    except paramiko.ssh_exception.AuthenticationException as e:
        login_failed = True
    
    pytest_assert(login_failed == True)

def ssh_run_command(ssh_client, command):
    stdin, stdout, stderr = ssh_client.exec_command(command, timeout=TIMEOUT_LIMIT)
    exit_code = stdout.channel.recv_exit_status()
    stdout_lines = stdout.readlines()
    stderr_lines = stderr.readlines()
    return exit_code, stdout_lines, stderr_lines

def check_ssh_output(res, exp_val):
    content_exist = False
    for l in res:
        if exp_val in l:
            content_exist = True
            break
    pytest_assert(content_exist)

@pytest.fixture
def remote_user_client(duthosts, enum_rand_one_per_hwsku_hostname, creds_all_duts):
    duthost = duthosts[enum_rand_one_per_hwsku_hostname]
    dutip = duthost.host.options['inventory_manager'].get_host(duthost.hostname).vars['ansible_host']
    ssh_client = ssh_connect_remote(dutip, creds_all_duts[duthost]['tacacs_authorization_user'],
                         creds_all_duts[duthost]['tacacs_authorization_user_passwd'])
    yield ssh_client
    ssh_client.close()

@pytest.fixture
def local_user_client(duthosts, enum_rand_one_per_hwsku_hostname, creds_all_duts):
    duthost = duthosts[enum_rand_one_per_hwsku_hostname]
    dutip = duthost.host.options['inventory_manager'].get_host(duthost.hostname).vars['ansible_host']
    ssh_client = paramiko.SSHClient()
    ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    yield ssh_client
    ssh_client.close()

@pytest.fixture(scope="module", autouse=True)
def check_image_version(duthost):
    """Skips this test if the SONiC image installed on DUT is older than 202112
    Args:
        duthost: Hostname of DUT.
    Returns:
        None.
    """
    skip_release(duthost, ["201811", "201911", "202012", "202106"])

def test_authorization_tacacs_only(localhost, duthosts, enum_rand_one_per_hwsku_hostname, creds_all_duts, check_tacacs, remote_user_client):
    duthost = duthosts[enum_rand_one_per_hwsku_hostname]
    duthost.shell("sudo config aaa authorization tacacs+")

    """
        Verify TACACS+ user run command in server side whitelist:
            If command have local permission, user can run command.
    """
    exit_code, stdout, stderr = ssh_run_command(remote_user_client, "show aaa")
    pytest_assert(exit_code == 0)
    check_ssh_output(stdout, 'AAA authentication')

    """
        Verify TACACS+ user run command in server side whitelist:
            If command not have local permission, user can't run command.
    """
    exit_code, stdout, stderr = ssh_run_command(remote_user_client, "config aaa")
    pytest_assert(exit_code == 1)
    check_ssh_output(stderr, 'Root privileges are required for this operation')

    # Verify TACACS+ user can't run command not in server side whitelist.
    exit_code, stdout, stderr = ssh_run_command(remote_user_client, "cat /etc/passwd")
    pytest_assert(exit_code == 1)
    check_ssh_output(stdout, '/usr/bin/cat authorize failed by TACACS+ with given arguments, not executing')

    # Verify Local user can't login.
    dutip = duthost.host.options['inventory_manager'].get_host(duthost.hostname).vars['ansible_host']
    check_ssh_connect_remote_failed(dutip, creds_all_duts[duthost]['local_user'],
                             creds_all_duts[duthost]['local_user_passwd'])

def test_authorization_tacacs_only_some_server_down(localhost, duthosts, enum_rand_one_per_hwsku_hostname, creds_all_duts,ptfhost, check_tacacs, remote_user_client):
    """
        Setup multiple tacacs server for this UT.
        Tacacs server 127.0.0.1 not accessible.
    """
    invalid_tacacs_server_ip = "127.0.0.1"
    duthost = duthosts[enum_rand_one_per_hwsku_hostname]
    tacacs_server_ip = ptfhost.host.options['inventory_manager'].get_host(ptfhost.hostname).vars['ansible_host']
    config_facts = duthost.config_facts(host=duthost.hostname, source="running")['ansible_facts']
    duthost.shell("sudo config tacacs timeout 1")
    for tacacs_server in config_facts.get('TACPLUS_SERVER', {}):
        duthost.shell("sudo config tacacs delete %s" % tacacs_server)
    duthost.shell("sudo config tacacs add %s" % invalid_tacacs_server_ip)
    duthost.shell("sudo config tacacs add %s" % tacacs_server_ip)
    
    """
        Verify TACACS+ user run command in server side whitelist:
            If command have local permission, user can run command.
            If command not have local permission, user can't run command.
        Verify TACACS+ user can't run command not in server side whitelist.
        Verify Local user can't login.
    """
    test_authorization_tacacs_only(localhost, duthosts, enum_rand_one_per_hwsku_hostname, creds_all_duts, check_tacacs, remote_user_client)

    # Cleanup
    duthost.shell("sudo config tacacs delete %s" % invalid_tacacs_server_ip)

def test_authorization_tacacs_only_then_server_down_after_login(localhost, duthosts, enum_rand_one_per_hwsku_hostname, creds_all_duts,ptfhost, check_tacacs, remote_user_client):
    duthost = duthosts[enum_rand_one_per_hwsku_hostname]
    duthost.shell("sudo config aaa authorization tacacs+")

    # Verify when server are accessible, TACACS+ user can run command in server side whitelist.
    exit_code, stdout, stderr = ssh_run_command(remote_user_client, "show aaa")
    pytest_assert(exit_code == 0)
    check_ssh_output(stdout, 'AAA authentication')

    # Shutdown tacacs server
    stop_tacacs_server(ptfhost)

    # Verify when server are not accessible, TACACS+ user can't run any command.
    exit_code, stdout, stderr = ssh_run_command(remote_user_client, "show aaa")
    pytest_assert(exit_code == 1)
    check_ssh_output(stdout, '/usr/local/bin/show not authorized by TACACS+ with given arguments, not executing')

    #  Cleanup UT.
    start_tacacs_server(ptfhost)

def test_authorization_tacacs_and_local(localhost, duthosts, enum_rand_one_per_hwsku_hostname, creds_all_duts,ptfhost, check_tacacs, remote_user_client):
    duthost = duthosts[enum_rand_one_per_hwsku_hostname]
    duthost.shell("sudo config aaa authorization \"tacacs+ local\"")

    """
        Verify TACACS+ user run command in server side whitelist:
            If command have local permission, user can run command.
    """
    exit_code, stdout, stderr = ssh_run_command(remote_user_client, "show aaa")
    pytest_assert(exit_code == 0)
    check_ssh_output(stdout, 'AAA authentication')

    """
        Verify TACACS+ user run command in server side whitelist:
            If command not have local permission, user can't run command.
    """
    exit_code, stdout, stderr = ssh_run_command(remote_user_client, "config aaa")
    pytest_assert(exit_code == 1)
    check_ssh_output(stderr, 'Root privileges are required for this operation')

    # Verify TACACS+ user can run command not in server side whitelist, but have local permission.
    exit_code, stdout, stderr = ssh_run_command(remote_user_client, "cat /etc/passwd")
    pytest_assert(exit_code == 0)
    check_ssh_output(stdout, 'root:x:0:0:root:/root:/bin/bash')

    # Verify Local user can't login.
    dutip = duthost.host.options['inventory_manager'].get_host(duthost.hostname).vars['ansible_host']
    check_ssh_connect_remote_failed(dutip, creds_all_duts[duthost]['local_user'],
                             creds_all_duts[duthost]['local_user_passwd'])


def test_authorization_tacacs_and_local_then_server_down_after_login(localhost, duthosts, enum_rand_one_per_hwsku_hostname, creds_all_duts,ptfhost, check_tacacs, remote_user_client, local_user_client):
    duthost = duthosts[enum_rand_one_per_hwsku_hostname]
    duthost.shell("sudo config aaa authorization \"tacacs+ local\"")

    # Shutdown tacacs server
    stop_tacacs_server(ptfhost)
    
    # Verify TACACS+ user can run command not in server side whitelist but have permission in local.
    exit_code, stdout, stderr = ssh_run_command(remote_user_client, "cat /etc/passwd")
    pytest_assert(exit_code == 0)
    check_ssh_output(stdout, 'root:x:0:0:root:/root:/bin/bash')
    
    # Verify TACACS+ user can't run command in server side whitelist also not have permission in local.
    exit_code, stdout, stderr = ssh_run_command(remote_user_client, "config tacacs")
    pytest_assert(exit_code == 1)
    check_ssh_output(stdout, '/usr/local/bin/config not authorized by TACACS+ with given arguments, not executing')
    check_ssh_output(stderr, 'Root privileges are required for this operation')

    # Verify Local user can login when tacacs closed, and run command with local permission.
    dutip = duthost.host.options['inventory_manager'].get_host(duthost.hostname).vars['ansible_host']
    local_user_client.connect(dutip, username=creds_all_duts[duthost]['local_user'],
                                        password=creds_all_duts[duthost]['local_user_passwd'],
                                        allow_agent=False, look_for_keys=False, auth_timeout=TIMEOUT_LIMIT)

    exit_code, stdout, stderr = ssh_run_command(local_user_client, "show aaa")
    pytest_assert(exit_code == 0)
    check_ssh_output(stdout, 'AAA authentication')

    # Start tacacs server
    start_tacacs_server(ptfhost)
    
    # Verify after Local user login, then server becomes accessible, Local user still can run command with local permission.
    exit_code, stdout, stderr = ssh_run_command(local_user_client, "show aaa")
    pytest_assert(exit_code == 0)
    check_ssh_output(stdout, 'AAA authentication')


def test_authorization_local(localhost, duthosts, enum_rand_one_per_hwsku_hostname, creds_all_duts,ptfhost, check_tacacs, remote_user_client, local_user_client):
    duthost = duthosts[enum_rand_one_per_hwsku_hostname]
    duthost.shell("sudo config aaa authorization local")

    """
        TACACS server up:
            Verify TACACS+ user can run command if have permission in local.
    """
    exit_code, stdout, stderr = ssh_run_command(remote_user_client, "show aaa")
    pytest_assert(exit_code == 0)
    check_ssh_output(stdout, 'AAA authentication')

    """
        TACACS server up:
            Verify TACACS+ user can't run command if not have permission in local.
    """
    exit_code, stdout, stderr = ssh_run_command(remote_user_client, "config aaa")
    pytest_assert(exit_code == 1)
    check_ssh_output(stderr, 'Root privileges are required for this operation')

    # Shutdown tacacs server.
    stop_tacacs_server(ptfhost)

    """
        TACACS server down:
            Verify Local user can login, and run command with local permission.
    """
    dutip = duthost.host.options['inventory_manager'].get_host(duthost.hostname).vars['ansible_host']
    local_user_client.connect(dutip, username=creds_all_duts[duthost]['local_user'],
                                        password=creds_all_duts[duthost]['local_user_passwd'],
                                        allow_agent=False, look_for_keys=False, auth_timeout=TIMEOUT_LIMIT)

    exit_code, stdout, stderr = ssh_run_command(local_user_client, "show aaa")
    pytest_assert(exit_code == 0)
    check_ssh_output(stdout, 'AAA authentication')
    
    # Cleanup
    start_tacacs_server(ptfhost)


def test_bypass_authorization(localhost, duthosts, enum_rand_one_per_hwsku_hostname, creds_all_duts,ptfhost, check_tacacs, remote_user_client):
    duthost = duthosts[enum_rand_one_per_hwsku_hostname]
    duthost.shell("sudo config aaa authorization tacacs+")

    """
        Verify user can't run script with sh/python with following command.
            python ./testscript.py
    """
    exit_code, stdout, stderr = ssh_run_command(remote_user_client, 'echo "" >> ./testscript.py')
    pytest_assert(exit_code == 0)
    exit_code, stdout, stderr = ssh_run_command(remote_user_client, "python ./testscript.py")
    pytest_assert(exit_code == 1)
    check_ssh_output(stdout, 'authorize failed by TACACS+ with given arguments, not executing')

    # Verify user can't run 'find' command with '-exec' parameter.
    exit_code, stdout, stderr = ssh_run_command(remote_user_client, "find . -type f -exec /bin/sh ;")
    pytest_assert(exit_code == 1)
    check_ssh_output(stdout, 'authorize failed by TACACS+ with given arguments, not executing')

    # Verify user can run 'find' command without '-exec' parameter.
    exit_code, stdout, stderr = ssh_run_command(remote_user_client, "find . /bin/sh")
    pytest_assert(exit_code == 0)
    check_ssh_output(stdout, '/bin/sh')

    """
        Verify user can't run command with loader:
            /lib/x86_64-linux-gnu/ld-linux-x86-64.so.2 sh
    """
    exit_code, stdout, stderr = ssh_run_command(remote_user_client, "/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2 sh")
    pytest_assert(exit_code == 1)
    check_ssh_output(stdout, 'authorize failed by TACACS+ with given arguments, not executing')

    """
        Verify user can't run command with prefix/quoting:
            \sh
            "sh"
            echo $(sh -c ls)
    """
    exit_code, stdout, stderr = ssh_run_command(remote_user_client, "\\sh")
    pytest_assert(exit_code == 1)
    check_ssh_output(stdout, 'authorize failed by TACACS+ with given arguments, not executing')
    
    exit_code, stdout, stderr = ssh_run_command(remote_user_client, '"sh"')
    pytest_assert(exit_code == 1)
    check_ssh_output(stdout, 'authorize failed by TACACS+ with given arguments, not executing')
    
    exit_code, stdout, stderr = ssh_run_command(remote_user_client, "echo $(sh -c ls)")
    # echo command will run success and return 0, but sh command will be blocked.
    pytest_assert(exit_code == 0)
    check_ssh_output(stdout, 'authorize failed by TACACS+ with given arguments, not executing')

def test_backward_compatibility_disable_authorization(localhost, duthosts, enum_rand_one_per_hwsku_hostname, creds_all_duts,ptfhost, check_tacacs, remote_user_client, local_user_client):
    duthost = duthosts[enum_rand_one_per_hwsku_hostname]
    duthost.shell("sudo config aaa authorization local")

    # Verify domain account can run command if have permission in local.
    exit_code, stdout, stderr = ssh_run_command(remote_user_client, "show aaa")
    pytest_assert(exit_code == 0)
    check_ssh_output(stdout, 'AAA authentication')

    # Shutdown tacacs server
    stop_tacacs_server(ptfhost)

    # Verify domain account can't login to device successfully.
    dutip = duthost.host.options['inventory_manager'].get_host(duthost.hostname).vars['ansible_host']
    check_ssh_connect_remote_failed(dutip, creds_all_duts[duthost]['tacacs_authorization_user'],
                         creds_all_duts[duthost]['tacacs_authorization_user_passwd'])

    # Verify local admin account can run command if have permission in local.
    dutip = duthost.host.options['inventory_manager'].get_host(duthost.hostname).vars['ansible_host']
    local_user_client.connect(dutip, username=creds_all_duts[duthost]['local_user'],
                                        password=creds_all_duts[duthost]['local_user_passwd'],
                                        allow_agent=False, look_for_keys=False, auth_timeout=TIMEOUT_LIMIT)

    exit_code, stdout, stderr = ssh_run_command(local_user_client, "show aaa")
    pytest_assert(exit_code == 0)
    check_ssh_output(stdout, 'AAA authentication')

    # Verify local admin account can't run command if not have permission in local.
    exit_code, stdout, stderr = ssh_run_command(local_user_client, "config aaa")
    pytest_assert(exit_code == 1)
    check_ssh_output(stderr, 'Root privileges are required for this operation')

    # cleanup
    start_tacacs_server(ptfhost)