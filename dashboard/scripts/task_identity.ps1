function Test-TaskPrincipalForCurrentUser {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$TaskUserId
    )

    try {
        $CurrentUserSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
        $TaskUserSid = [System.Security.Principal.NTAccount]::new($TaskUserId).Translate(
            [System.Security.Principal.SecurityIdentifier]
        ).Value
        return [string]::Equals(
            $CurrentUserSid,
            $TaskUserSid,
            [StringComparison]::OrdinalIgnoreCase
        )
    } catch {
        return $false
    }
}
