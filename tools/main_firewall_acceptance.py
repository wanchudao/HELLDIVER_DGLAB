"""Final-EXE readonly permission checks; never creates a rule or invokes UAC."""


def run():
    import sys
    from src import firewall_access as access
    node = access.bundled_node()
    scripts = [access.query_script(node), *(access.mutation_script(node, op) for op in access.OPERATIONS)]
    commands = {
        'New-NetFirewallRule': ('Name', 'Group', 'Description', 'Enabled', 'Profile', 'Direction',
            'Action', 'Program', 'Protocol', 'LocalPort', 'RemotePort', 'LocalAddress', 'RemoteAddress',
            'EdgeTraversalPolicy', 'Authentication', 'Encryption', 'OverrideBlockRules', 'PolicyStore'),
        'Set-NetFirewallRule': ('Name', 'Enabled', 'Profile', 'Direction', 'Action', 'Program', 'Protocol',
            'LocalPort', 'RemotePort', 'LocalAddress', 'RemoteAddress', 'EdgeTraversalPolicy',
            'Authentication', 'Encryption', 'OverrideBlockRules', 'PolicyStore'),
        'Remove-NetFirewallRule': ('Name', 'PolicyStore'),
    }
    script = "$ErrorActionPreference='Stop'\nImport-Module NetSecurity\n"
    for content in scripts:
        script += "$tokens=$null;$errors=$null\n[void][System.Management.Automation.Language.Parser]::ParseInput(" + access._quote(content) + ", [ref]$tokens, [ref]$errors)\n"
        script += "if (@($errors).Count) { throw 'Permission script parse failed' }\n"
    for name, parameters in commands.items():
        script += "$command=Get-Command " + name + "\n"
        for param in parameters:
            script += "if (-not $command.Parameters.ContainsKey(" + access._quote(param) + ")) { throw 'Required NetSecurity parameter missing' }\n"
    script += "[pscustomobject]@{status='ok';scripts=3;cmdlets=3} | ConvertTo-Json -Compress\n"
    metadata = access.run_powershell(script)
    assert metadata == {'status': 'ok', 'scripts': 3, 'cmdlets': 3}
    status = access.query_status()
    return {'status': 'ok', 'frozen': bool(getattr(sys, 'frozen', False)),
            'backend': str(node), 'policy': access.policy(node), 'readonly_status': status,
            'script_syntax': True, 'netsecurity_parameters': True,
            'firewall_modified': False, 'real_uac_tested': False,
            'app_connected': False, 'game_capture_started': False}
