# Diagnóstico opcional: a tela Administração > Impressora já lista as mesmas
# impressoras automaticamente e permite salvar/testar a escolha.
try {
    Get-Printer -ErrorAction Stop |
        Select-Object Name, DriverName, PortName, Default |
        Format-Table -AutoSize
}
catch {
    Get-CimInstance Win32_Printer |
        Select-Object Name, DriverName, PortName, Default |
        Format-Table -AutoSize
}
Read-Host 'Pressione Enter para fechar'
