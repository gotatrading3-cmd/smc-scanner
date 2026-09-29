' Lance la ligne de commande recue en argument SANS aucune fenetre visible (style 0 = cachee).
' Utilise pour que ni GOTA_TRADING.cmd ni run_signal_dashboard.cmd ne fassent jamais apparaitre
' une invite de commandes a l'ecran, meme un instant.
Set objShell = CreateObject("WScript.Shell")
objShell.Run """" & WScript.Arguments(0) & """", 0, False
