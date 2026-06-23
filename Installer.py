"""
Installer.py — Universal Software Installer
=============================================
Layered resolution — always finds something or gives a clear answer:

Layer 1: Local DB + fuzzy match  (instant, offline, ~150 apps)
Layer 2: LLM resolution          (Groq → Ollama, if available)
Layer 3: Live package manager search (winget/apt/brew search)
Layer 4: PyPI search             (Python packages)
Layer 5: Clear failure message   (with real download link from web)

Install chain per OS:
  Windows : pip → winget → MS Store → choco → scoop → npm → cargo → go
  Linux   : pip → apt → snap → flatpak → dnf → pacman → zypper → npm → cargo → go
  macOS   : pip → brew cask → brew formula → mas → npm → cargo → go
"""

import subprocess
import sys
import shutil
import platform
import re
import json
import urllib.request
import urllib.parse
from dataclasses import dataclass
from difflib import get_close_matches
from typing import Optional

from logger import logger, log_step

OS = platform.system()


# ══════════════════════════════════════════════════════════════════════════════
# LOCAL APP DATABASE  (~150 apps, each with every package manager ID)
# Key = pipe-separated aliases (all lowercase, include common typos)
# ══════════════════════════════════════════════════════════════════════════════

_DB_RAW: dict[str, dict] = {
    # ── Browsers ──────────────────────────────────────────────────────────────
    "chrome|google chrome|googlechrome|chroem|chrme|gogle chrome|goggle chrome": {
        "canonical":"Google Chrome","winget":"Google.Chrome","msstore":"XPFCG9B3T4SFDR",
        "choco":"googlechrome","scoop":"extras/googlechrome","apt":"google-chrome-stable",
        "snap":"chromium","flatpak":"com.google.Chrome","brew_cask":"google-chrome",
        "category":"browser","download":"https://www.google.com/chrome/"},
    "firefox|mozila firefox|fierfox|firfox|ffox|firefix": {
        "canonical":"Mozilla Firefox","winget":"Mozilla.Firefox","choco":"firefox",
        "scoop":"extras/firefox","apt":"firefox","snap":"firefox",
        "flatpak":"org.mozilla.firefox","brew_cask":"firefox",
        "category":"browser","download":"https://www.mozilla.org/firefox/"},
    "brave|brave browser|brav browser|brav": {
        "canonical":"Brave Browser","winget":"Brave.Brave","choco":"brave",
        "apt":"brave-browser","flatpak":"com.brave.Browser","brew_cask":"brave-browser",
        "category":"browser","download":"https://brave.com/download/"},
    "edge|microsoft edge|ms edge|msedge": {
        "canonical":"Microsoft Edge","winget":"Microsoft.Edge","choco":"microsoft-edge",
        "apt":"microsoft-edge-stable","brew_cask":"microsoft-edge",
        "category":"browser","download":"https://www.microsoft.com/edge"},
    "opera": {
        "canonical":"Opera","winget":"Opera.Opera","choco":"opera","apt":"opera-stable",
        "brew_cask":"opera","category":"browser","download":"https://www.opera.com/download"},
    "tor|tor browser|torbrowser": {
        "canonical":"Tor Browser","winget":"TorProject.TorBrowser","choco":"tor-browser",
        "flatpak":"org.torproject.torbrowser-launcher","brew_cask":"tor-browser",
        "category":"browser","download":"https://www.torproject.org/download/"},

    # ── Communication ──────────────────────────────────────────────────────────
    "discord|discrod|discort|discrd|disord|dissord": {
        "canonical":"Discord","winget":"Discord.Discord","msstore":"XPDC2RH70K22MN",
        "choco":"discord","scoop":"extras/discord","apt":"discord","snap":"discord",
        "flatpak":"com.discordapp.Discord","brew_cask":"discord",
        "category":"communication","download":"https://discord.com/download"},
    "slack|slak|slck|slacck": {
        "canonical":"Slack","winget":"SlackTechnologies.Slack","msstore":"9WZDNCRDK8WW",
        "choco":"slack","snap":"slack --classic","flatpak":"com.slack.Slack",
        "brew_cask":"slack","category":"communication","download":"https://slack.com/downloads"},
    "zoom|zooom|zom|zomm": {
        "canonical":"Zoom","winget":"Zoom.Zoom","choco":"zoom","apt":"zoom",
        "flatpak":"us.zoom.Zoom","brew_cask":"zoom",
        "category":"communication","download":"https://zoom.us/download"},
    "teams|microsoft teams|ms teams|msteams|msteam": {
        "canonical":"Microsoft Teams","winget":"Microsoft.Teams","msstore":"XPFBBS0ZS9RGPD",
        "choco":"microsoft-teams","apt":"teams","snap":"teams",
        "flatpak":"com.microsoft.Teams","brew_cask":"microsoft-teams",
        "category":"communication","download":"https://www.microsoft.com/microsoft-teams/download-app"},
    "telegram|telgram|telegramm|telegarm": {
        "canonical":"Telegram","winget":"Telegram.TelegramDesktop","msstore":"9NZTWSQNTD0S",
        "choco":"telegram","apt":"telegram-desktop","snap":"telegram-desktop",
        "flatpak":"org.telegram.desktop","brew_cask":"telegram","mas":"747648890",
        "category":"communication","download":"https://telegram.org/"},
    "whatsapp|watsapp|whatsap|whtsapp": {
        "canonical":"WhatsApp","winget":"WhatsApp.WhatsApp","msstore":"9NKSQGP7F2NH",
        "choco":"whatsapp","flatpak":"io.github.mimbrero.WhatsAppDesktop",
        "brew_cask":"whatsapp","mas":"1147396723",
        "category":"communication","download":"https://www.whatsapp.com/download"},
    "skype|skipe|skyp": {
        "canonical":"Skype","winget":"Microsoft.Skype","msstore":"9WZDNCRFJ364",
        "choco":"skype","apt":"skypeforlinux","snap":"skype --classic",
        "flatpak":"com.skype.Client","brew_cask":"skype","mas":"304878510",
        "category":"communication","download":"https://www.skype.com/download"},
    "signal|sigal": {
        "canonical":"Signal","winget":"OpenWhisperSystems.Signal","choco":"signal",
        "apt":"signal-desktop","flatpak":"org.signal.Signal","brew_cask":"signal",
        "category":"communication","download":"https://signal.org/download/"},

    # ── Dev Tools ──────────────────────────────────────────────────────────────
    "vscode|vs code|visual studio code|vscoed|vsocde|vscde|vscoe|visualstudiocode": {
        "canonical":"Visual Studio Code","winget":"Microsoft.VisualStudioCode",
        "msstore":"XP9KHM4BK9FZ7Q","choco":"vscode","scoop":"extras/vscode",
        "apt":"code","snap":"code --classic","flatpak":"com.visualstudio.code",
        "brew_cask":"visual-studio-code","category":"dev_tool",
        "download":"https://code.visualstudio.com/download"},
    "git|gitt|gti": {
        "canonical":"Git","winget":"Git.Git","choco":"git","scoop":"git",
        "apt":"git","dnf":"git","pacman":"git","brew_formula":"git",
        "category":"dev_tool","download":"https://git-scm.com/downloads"},
    "github desktop|githubdesktop|gh desktop": {
        "canonical":"GitHub Desktop","winget":"GitHub.GitHubDesktop",
        "choco":"github-desktop","brew_cask":"github",
        "category":"dev_tool","download":"https://desktop.github.com/"},
    "nodejs|node|node.js|nod|ndoe|nodjes|nodjs": {
        "canonical":"Node.js","winget":"OpenJS.NodeJS","choco":"nodejs","scoop":"nodejs",
        "apt":"nodejs","snap":"node --classic","dnf":"nodejs","pacman":"nodejs",
        "brew_formula":"node","category":"dev_tool","download":"https://nodejs.org/download/"},
    "python|python3|pyhton|pytohn|pythn|pyton": {
        "canonical":"Python 3","winget":"Python.Python.3","msstore":"9PJPW5LDXLZ5",
        "choco":"python","scoop":"python","apt":"python3","dnf":"python3","pacman":"python",
        "brew_formula":"python","category":"dev_tool","download":"https://www.python.org/downloads/"},
    "java|jdk|java development kit|jva": {
        "canonical":"Java JDK","winget":"Oracle.JDK.21","choco":"openjdk",
        "apt":"default-jdk","dnf":"java-latest-openjdk","pacman":"jdk-openjdk",
        "brew_formula":"openjdk","category":"dev_tool","download":"https://www.java.com/download/"},
    "rust|rustlang|rust language|rustt": {
        "canonical":"Rust","winget":"Rustlang.Rustup","choco":"rust",
        "apt":"rustup","dnf":"rust","pacman":"rust","brew_formula":"rust",
        "category":"dev_tool","download":"https://www.rust-lang.org/tools/install"},
    "go|golang|go lang|golan": {
        "canonical":"Go","winget":"GoLang.Go","choco":"golang","scoop":"go",
        "apt":"golang","dnf":"golang","pacman":"go","brew_formula":"go",
        "category":"dev_tool","download":"https://go.dev/dl/"},
    "ruby|rubyy": {
        "canonical":"Ruby","winget":"RubyInstallerTeam.Ruby","choco":"ruby",
        "apt":"ruby","dnf":"ruby","pacman":"ruby","brew_formula":"ruby",
        "category":"dev_tool","download":"https://www.ruby-lang.org/downloads/"},
    "php|phpp": {
        "canonical":"PHP","winget":"PHP.PHP","choco":"php",
        "apt":"php","dnf":"php","pacman":"php","brew_formula":"php",
        "category":"dev_tool","download":"https://www.php.net/downloads"},
    "docker|dokcer|docekr|docker desktop": {
        "canonical":"Docker Desktop","winget":"Docker.DockerDesktop",
        "choco":"docker-desktop","apt":"docker.io","snap":"docker",
        "flatpak":"io.docker.DockerDesktop","brew_cask":"docker",
        "category":"dev_tool","download":"https://www.docker.com/products/docker-desktop/"},
    "postman|postamn|potsman|postmaan": {
        "canonical":"Postman","winget":"Postman.Postman","choco":"postman",
        "apt":"postman","snap":"postman","flatpak":"com.getpostman.Postman",
        "brew_cask":"postman","category":"dev_tool","download":"https://www.postman.com/downloads/"},
    "insomnia|insomnnia": {
        "canonical":"Insomnia","winget":"Insomnia.Insomnia",
        "choco":"insomnia-rest-api-client","flatpak":"rest.insomnia.Insomnia",
        "brew_cask":"insomnia","category":"dev_tool","download":"https://insomnia.rest/download"},
    "android studio|androidstudio|android stdio": {
        "canonical":"Android Studio","winget":"Google.AndroidStudio","choco":"androidstudio",
        "flatpak":"com.google.AndroidStudio","brew_cask":"android-studio",
        "category":"dev_tool","download":"https://developer.android.com/studio"},
    "cmake|cmkae": {
        "canonical":"CMake","winget":"Kitware.CMake","choco":"cmake",
        "apt":"cmake","dnf":"cmake","pacman":"cmake","brew_formula":"cmake",
        "category":"dev_tool","download":"https://cmake.org/download/"},
    "ffmpeg|ffmepg|ffmeg": {
        "canonical":"FFmpeg","winget":"Gyan.FFmpeg","choco":"ffmpeg","scoop":"ffmpeg",
        "apt":"ffmpeg","dnf":"ffmpeg","pacman":"ffmpeg","brew_formula":"ffmpeg",
        "category":"dev_tool","download":"https://ffmpeg.org/download.html"},
    "kubectl|kubctl|kubectll": {
        "canonical":"kubectl","winget":"Kubernetes.kubectl","choco":"kubernetes-cli",
        "scoop":"kubectl","apt":"kubectl","brew_formula":"kubectl",
        "category":"dev_tool","download":"https://kubernetes.io/docs/tasks/tools/"},
    "terraform|terrafrom": {
        "canonical":"Terraform","winget":"Hashicorp.Terraform","choco":"terraform",
        "scoop":"terraform","apt":"terraform","brew_formula":"terraform",
        "category":"dev_tool","download":"https://developer.hashicorp.com/terraform/install"},
    "gh|github cli|githubcli": {
        "canonical":"GitHub CLI","winget":"GitHub.cli","choco":"gh","scoop":"gh",
        "apt":"gh","dnf":"gh","pacman":"github-cli","brew_formula":"gh",
        "category":"dev_tool","download":"https://cli.github.com/"},
    "ollama|olama|ollamma": {
        "canonical":"Ollama","winget":"Ollama.Ollama","choco":"ollama",
        "apt":"ollama","brew_cask":"ollama",
        "category":"dev_tool","download":"https://ollama.com/download"},
    "wsl|wsl2|windows subsystem for linux": {
        "canonical":"WSL2","winget":"Microsoft.WSL","choco":"wsl2",
        "category":"system_tool","download":"https://learn.microsoft.com/windows/wsl/install"},
    "nvm|node version manager": {
        "canonical":"NVM for Windows","winget":"CoreyButler.NVMforWindows",
        "choco":"nvm","brew_formula":"nvm",
        "category":"dev_tool","download":"https://github.com/coreybutler/nvm-windows"},
    "yarn": {
        "canonical":"Yarn","winget":"Yarn.Yarn","choco":"yarn","scoop":"yarn",
        "apt":"yarn","brew_formula":"yarn","npm":"yarn",
        "category":"dev_tool","download":"https://yarnpkg.com/getting-started/install"},
    "pnpm": {
        "canonical":"pnpm","winget":"pnpm.pnpm","choco":"pnpm","scoop":"pnpm",
        "apt":"pnpm","brew_formula":"pnpm","npm":"pnpm",
        "category":"dev_tool","download":"https://pnpm.io/installation"},
    "awscli|aws cli|aws": {
        "canonical":"AWS CLI","winget":"Amazon.AWSCLI","choco":"awscli","scoop":"aws",
        "apt":"awscli","brew_formula":"awscli",
        "category":"dev_tool","download":"https://aws.amazon.com/cli/"},
    "wireshark|wire shark|wirshark": {
        "canonical":"Wireshark","winget":"WiresharkFoundation.Wireshark","choco":"wireshark",
        "apt":"wireshark","dnf":"wireshark","pacman":"wireshark-qt",
        "brew_cask":"wireshark","flatpak":"org.wireshark.Wireshark",
        "category":"dev_tool","download":"https://www.wireshark.org/download.html"},
    "nmap|namp": {
        "canonical":"Nmap","winget":"Insecure.Nmap","choco":"nmap",
        "apt":"nmap","dnf":"nmap","pacman":"nmap","brew_formula":"nmap",
        "category":"dev_tool","download":"https://nmap.org/download.html"},
    "virtualbox|virtual box|virtalbox": {
        "canonical":"VirtualBox","winget":"Oracle.VirtualBox","choco":"virtualbox",
        "apt":"virtualbox","dnf":"virtualbox","brew_cask":"virtualbox",
        "category":"utility","download":"https://www.virtualbox.org/wiki/Downloads"},
    "tesseract|tesseract ocr|tesseract-ocr": {
        "canonical":"Tesseract OCR","winget":"UB-Mannheim.TesseractOCR","choco":"tesseract",
        "apt":"tesseract-ocr","dnf":"tesseract","pacman":"tesseract","brew_formula":"tesseract",
        "category":"dev_tool","download":"https://github.com/UB-Mannheim/tesseract/wiki"},

    # ── Databases ──────────────────────────────────────────────────────────────
    "postgresql|postgres|postgre|postgressql|psql|postgress": {
        "canonical":"PostgreSQL","winget":"PostgreSQL.PostgreSQL","choco":"postgresql",
        "apt":"postgresql","dnf":"postgresql-server","pacman":"postgresql",
        "brew_formula":"postgresql","category":"database",
        "download":"https://www.postgresql.org/download/"},
    "pgadmin|pgadmin4|pg admin": {
        "canonical":"pgAdmin 4","winget":"PostgreSQL.pgAdmin","choco":"pgadmin4",
        "apt":"pgadmin4","brew_cask":"pgadmin4",
        "category":"database","download":"https://www.pgadmin.org/download/"},
    "mysql|mysqul|my sql|mysql server": {
        "canonical":"MySQL","winget":"Oracle.MySQL","choco":"mysql",
        "apt":"mysql-server","dnf":"mysql-server","pacman":"mysql","brew_formula":"mysql",
        "category":"database","download":"https://dev.mysql.com/downloads/"},
    "mongodb|mongo db|mongdb|mangodb": {
        "canonical":"MongoDB","winget":"MongoDB.Server","choco":"mongodb",
        "apt":"mongodb","dnf":"mongodb-org","brew_formula":"mongodb-community",
        "category":"database","download":"https://www.mongodb.com/try/download/community"},
    "redis|rediss|rdis": {
        "canonical":"Redis","winget":"Redis.Redis","choco":"redis-64",
        "apt":"redis-server","dnf":"redis","pacman":"redis","brew_formula":"redis",
        "category":"database","download":"https://redis.io/download/"},

    # ── Media ──────────────────────────────────────────────────────────────────
    "vlc|vls|vlc media player|vlic": {
        "canonical":"VLC Media Player","winget":"VideoLAN.VLC","msstore":"XPDM1ZW6815MQM",
        "choco":"vlc","scoop":"vlc","apt":"vlc","snap":"vlc",
        "flatpak":"org.videolan.VLC","brew_cask":"vlc","mas":"126153474",
        "category":"media","download":"https://www.videolan.org/vlc/"},
    "spotify|spotfy|spottify": {
        "canonical":"Spotify","winget":"Spotify.Spotify","msstore":"9NCBCSZSJRSB",
        "choco":"spotify","apt":"spotify-client","snap":"spotify",
        "flatpak":"com.spotify.Client","brew_cask":"spotify","mas":"324684580",
        "category":"media","download":"https://www.spotify.com/download/"},
    "obs|obs studio|obstudio|obs-studio": {
        "canonical":"OBS Studio","winget":"OBSProject.OBSStudio","choco":"obs-studio",
        "apt":"obs-studio","flatpak":"com.obsproject.Studio","brew_cask":"obs",
        "category":"media","download":"https://obsproject.com/download"},
    "audacity|audacsity|audacty|audicity": {
        "canonical":"Audacity","winget":"Audacity.Audacity","choco":"audacity",
        "apt":"audacity","flatpak":"org.audacityteam.Audacity","brew_cask":"audacity",
        "category":"media","download":"https://www.audacityteam.org/download/"},
    "handbrake|hand brake|handbreak": {
        "canonical":"HandBrake","winget":"HandBrake.HandBrake","choco":"handbrake",
        "apt":"handbrake","flatpak":"fr.handbrake.ghb","brew_cask":"handbrake",
        "category":"media","download":"https://handbrake.fr/downloads.php"},
    "kdenlive|kden live": {
        "canonical":"Kdenlive","winget":"KDE.Kdenlive","choco":"kdenlive",
        "apt":"kdenlive","flatpak":"org.kde.kdenlive","brew_cask":"kdenlive",
        "category":"media","download":"https://kdenlive.org/download/"},
    "davinci resolve|davinci|davinchi resolve|davnci": {
        "canonical":"DaVinci Resolve","winget":"Blackmagic.DaVinciResolve",
        "choco":"davinci-resolve","flatpak":"com.blackmagicdesign.resolve",
        "brew_cask":"davinci-resolve",
        "category":"media","download":"https://www.blackmagicdesign.com/products/davinciresolve"},
    "foobar2000|foobar|foobar 2000": {
        "canonical":"foobar2000","winget":"PeterPawlowski.foobar2000","choco":"foobar2000",
        "category":"media","download":"https://www.foobar2000.org/download"},
    "musicbee|music bee": {
        "canonical":"MusicBee","winget":"MusicBee.MusicBee","msstore":"9P4CLT2RJ1RS",
        "choco":"musicbee",
        "category":"media","download":"https://getmusicbee.com/downloads/"},

    # ── Graphics / Design ─────────────────────────────────────────────────────
    "gimp|gim|gimmp|gimp photo": {
        "canonical":"GIMP","winget":"GIMP.GIMP","choco":"gimp","apt":"gimp",
        "snap":"gimp","flatpak":"org.gimp.GIMP","brew_cask":"gimp",
        "category":"design","download":"https://www.gimp.org/downloads/"},
    "inkscape|inkscpae|inkscap": {
        "canonical":"Inkscape","winget":"Inkscape.Inkscape","choco":"inkscape",
        "apt":"inkscape","flatpak":"org.inkscape.Inkscape","brew_cask":"inkscape",
        "category":"design","download":"https://inkscape.org/release/"},
    "blender|blnder|blendr": {
        "canonical":"Blender","winget":"BlenderFoundation.Blender","msstore":"9PP3C07GTVRH",
        "choco":"blender","apt":"blender","snap":"blender --classic",
        "flatpak":"org.blender.Blender","brew_cask":"blender",
        "category":"design","download":"https://www.blender.org/download/"},
    "krita|krita paint|krta": {
        "canonical":"Krita","winget":"KDE.Krita","msstore":"9N6X57ZGRXGX",
        "choco":"krita","apt":"krita","flatpak":"org.kde.krita","brew_cask":"krita",
        "category":"design","download":"https://krita.org/download/"},
    "figma|figmma|fgima": {
        "canonical":"Figma","winget":"Figma.Figma","choco":"figma",
        "flatpak":"io.github.Figma_Linux.figma_linux","brew_cask":"figma",
        "category":"design","download":"https://www.figma.com/downloads/"},
    "irfanview|irfan view|irfanvew": {
        "canonical":"IrfanView","winget":"IrfanSkiljan.IrfanView","choco":"irfanview",
        "category":"design","download":"https://www.irfanview.com/main_download_engl.htm"},

    # ── Screenshots / Screen Tools ────────────────────────────────────────────
    "sharex|share x|shar ex": {
        "canonical":"ShareX","winget":"ShareX.ShareX","msstore":"9NBLGGH4Z1SP",
        "choco":"sharex","scoop":"extras/sharex",
        "category":"utility","download":"https://getsharex.com/downloads"},
    "greenshot|green shot": {
        "canonical":"Greenshot","winget":"Greenshot.Greenshot","choco":"greenshot",
        "category":"utility","download":"https://getgreenshot.org/downloads/"},
    "flameshot|flame shot": {
        "canonical":"Flameshot","winget":"Flameshot.Flameshot","choco":"flameshot",
        "apt":"flameshot","flatpak":"org.flameshot.Flameshot","brew_cask":"flameshot",
        "category":"utility","download":"https://flameshot.org/#download"},
    "lightshot|light shot": {
        "canonical":"Lightshot","winget":"Skillbrains.Lightshot","choco":"lightshot",
        "category":"utility","download":"https://app.prntscr.com/en/download.html"},

    # ── AI / ML Tools ─────────────────────────────────────────────────────────
    "whisper|openai whisper|wisper|whsiper": {
        "canonical":"OpenAI Whisper","pip":"openai-whisper","is_python_package":True,
        "category":"python_library","download":"https://github.com/openai/whisper"},
    "whisperflow|wisperflow|whsiperflow|whisper flow": {
        "canonical":"WhisperFlow",
        "pip":"whisperflow",
        "is_python_package": True,
        "category":"python_library",
        "download":"https://pypi.org/search/?q=whisperflow",
        "_note":"Niche app — tries pip first, then live winget/apt search"},
    "stable diffusion|stablediffusion|stable difusion|stabel diffusion": {
        "canonical":"Stable Diffusion (AUTOMATIC1111)","pip":"",
        "winget":"","choco":"",
        "category":"other",
        "download":"https://github.com/AUTOMATIC1111/stable-diffusion-webui#installation-and-running"},
    "lm studio|lmstudio|lm-studio": {
        "canonical":"LM Studio","winget":"LMStudio.LMStudio",
        "brew_cask":"lm-studio",
        "category":"dev_tool","download":"https://lmstudio.ai/"},
    "jan|jan ai|jan.ai": {
        "canonical":"Jan AI","winget":"Homebrew.Brew",
        "brew_cask":"jan",
        "category":"dev_tool","download":"https://jan.ai/download"},
    "pinokio|pinokio ai": {
        "canonical":"Pinokio","winget":"Pinokio.Pinokio",
        "brew_cask":"pinokio",
        "category":"dev_tool","download":"https://pinokio.computer/"},

    # ── Games & Launchers ──────────────────────────────────────────────────────
    "steam|steeem|steem|stam": {
        "canonical":"Steam","winget":"Valve.Steam","choco":"steam",
        "apt":"steam","flatpak":"com.valvesoftware.Steam","brew_cask":"steam",
        "category":"game","download":"https://store.steampowered.com/about/"},
    "epic games|epic games launcher|epicgames|epic|epik games": {
        "canonical":"Epic Games Launcher","winget":"EpicGames.EpicGamesLauncher",
        "choco":"epicgameslauncher","flatpak":"com.heroicgameslauncher.hgl",
        "brew_cask":"epic-games","category":"game","download":"https://store.epicgames.com/download"},
    "minecraft|mincraft|minecraf|minecrft|mineecraft": {
        "canonical":"Minecraft Launcher","winget":"Mojang.MinecraftLauncher",
        "msstore":"9NBLGGH537BL","choco":"minecraft-launcher",
        "flatpak":"com.mojang.Minecraft","brew_cask":"minecraft",
        "category":"game","download":"https://www.minecraft.net/download"},
    "fortnite|fortnit|fortnite game|fortnte": {
        "canonical":"Fortnite (via Epic Games)","winget":"EpicGames.EpicGamesLauncher",
        "flatpak":"com.heroicgameslauncher.hgl","brew_cask":"epic-games",
        "category":"game","download":"https://store.epicgames.com/fortnite"},
    "gta5|gta 5|grand theft auto 5|grand theft auto v|gta v|gtav": {
        "canonical":"Grand Theft Auto V","is_uninstallable":True,
        "uninstallable_reason":"GTA V is a paid game. Buy and download via Steam: https://store.steampowered.com/app/271590 or Rockstar: https://www.rockstargames.com/gta-v",
        "category":"game","download":"https://store.steampowered.com/app/271590"},
    "valorant|valaront|valrant|volarant": {
        "canonical":"Valorant","winget":"RiotGames.Valorant.AP",
        "category":"game","download":"https://playvalorant.com/download/"},
    "league of legends|lol|leauge of legends|lol game": {
        "canonical":"League of Legends","winget":"RiotGames.LeagueOfLegends.NA",
        "category":"game","download":"https://signup.leagueoflegends.com/download"},
    "roblox|roblx|roblox game": {
        "canonical":"Roblox","winget":"Roblox.Roblox","msstore":"9NBLGGGZM6WM",
        "category":"game","download":"https://www.roblox.com/download"},
    "rockstar games launcher|rockstar launcher": {
        "canonical":"Rockstar Games Launcher","winget":"Rockstar.RockstarGamesLauncher",
        "category":"game","download":"https://socialclub.rockstargames.com/rockstar-games-launcher"},

    # ── Utilities ──────────────────────────────────────────────────────────────
    "7zip|7-zip|7 zip|sevn zip": {
        "canonical":"7-Zip","winget":"7zip.7zip","choco":"7zip","scoop":"7zip",
        "apt":"p7zip-full","dnf":"p7zip","pacman":"p7zip","brew_formula":"p7zip",
        "category":"utility","download":"https://www.7-zip.org/download.html"},
    "winrar|win rar|winrr": {
        "canonical":"WinRAR","winget":"RARLab.WinRAR","choco":"winrar",
        "category":"utility","download":"https://www.win-rar.com/download.html"},
    "notepad++|notepadpp|notepad plus plus|notepadplusplus": {
        "canonical":"Notepad++","winget":"Notepad++.Notepad++","choco":"notepadplusplus",
        "scoop":"notepadplusplus","category":"utility",
        "download":"https://notepad-plus-plus.org/downloads/"},
    "notion|noton|nootion": {
        "canonical":"Notion","winget":"Notion.Notion","choco":"notion",
        "flatpak":"md.obsidian.Obsidian","brew_cask":"notion","mas":"1559269364",
        "category":"utility","download":"https://www.notion.so/desktop"},
    "obsidian|obsidain|obisidian": {
        "canonical":"Obsidian","winget":"Obsidian.Obsidian","choco":"obsidian",
        "flatpak":"md.obsidian.Obsidian","brew_cask":"obsidian","mas":"1547905921",
        "category":"utility","download":"https://obsidian.md/download"},
    "bitwarden|bitwrden|bitwardeen": {
        "canonical":"Bitwarden","winget":"Bitwarden.Bitwarden","msstore":"9PJSDV0VPK04",
        "choco":"bitwarden","snap":"bitwarden","flatpak":"com.bitwarden.desktop",
        "brew_cask":"bitwarden","mas":"1352778147",
        "category":"utility","download":"https://bitwarden.com/download/"},
    "powertoys|power toys|powertoy": {
        "canonical":"Microsoft PowerToys","winget":"Microsoft.PowerToys",
        "msstore":"XP89DCGQ3K6VLD","choco":"powertoys","scoop":"extras/powertoys",
        "category":"utility","download":"https://github.com/microsoft/PowerToys/releases"},
    "rufus|rufues": {
        "canonical":"Rufus","winget":"Rufus.Rufus","choco":"rufus","scoop":"extras/rufus",
        "category":"utility","download":"https://rufus.ie/downloads/"},
    "etcher|balena etcher": {
        "canonical":"balenaEtcher","winget":"Balena.Etcher","choco":"etcher",
        "apt":"balena-etcher-electron","brew_cask":"balenaetcher",
        "category":"utility","download":"https://etcher.balena.io/#download-etcher"},
    "everything|everthing search|everything search": {
        "canonical":"Everything Search","winget":"voidtools.Everything",
        "choco":"everything","scoop":"everything",
        "category":"utility","download":"https://www.voidtools.com/downloads/"},
    "libreoffice|libre office|liberoffice": {
        "canonical":"LibreOffice","winget":"TheDocumentFoundation.LibreOffice",
        "choco":"libreoffice-fresh","apt":"libreoffice","snap":"libreoffice",
        "flatpak":"org.libreoffice.LibreOffice","brew_cask":"libreoffice",
        "category":"utility","download":"https://www.libreoffice.org/download/download-libreoffice/"},
    "thunderbird|thunder bird|thunderbrd": {
        "canonical":"Mozilla Thunderbird","winget":"Mozilla.Thunderbird","choco":"thunderbird",
        "apt":"thunderbird","flatpak":"org.mozilla.Thunderbird",
        "brew_cask":"thunderbird","mas":"1176895641",
        "category":"utility","download":"https://www.thunderbird.net/download/"},
    "filezilla|file zilla|filzilla": {
        "canonical":"FileZilla","winget":"TimKosse.FileZilla.Client","choco":"filezilla",
        "apt":"filezilla","flatpak":"org.filezillaproject.Filezilla",
        "brew_cask":"filezilla","category":"utility","download":"https://filezilla-project.org/"},
    "putty|puty|ptty": {
        "canonical":"PuTTY","winget":"PuTTY.PuTTY","choco":"putty","scoop":"putty",
        "apt":"putty","brew_cask":"putty",
        "category":"utility","download":"https://www.putty.org/"},
    "winscp|win scp|winspc": {
        "canonical":"WinSCP","winget":"WinSCP.WinSCP","choco":"winscp","scoop":"winscp",
        "category":"utility","download":"https://winscp.net/eng/download.php"},
    "drawio|draw.io|draw io": {
        "canonical":"draw.io Desktop","winget":"JGraph.Draw","choco":"drawio",
        "brew_cask":"drawio","category":"utility","download":"https://github.com/jgraph/drawio-desktop/releases"},
    "vlc|vls": {
        "canonical":"VLC Media Player","winget":"VideoLAN.VLC",
        "category":"media","download":"https://www.videolan.org/vlc/"},
    "cpuz|cpu-z|cpu z": {
        "canonical":"CPU-Z","winget":"CPUID.CPU-Z","choco":"cpu-z","scoop":"extras/cpu-z",
        "category":"utility","download":"https://www.cpuid.com/softwares/cpu-z.html"},
    "hwinfo|hw info|hwinfo64": {
        "canonical":"HWiNFO","winget":"REALiX.HWiNFO","choco":"hwinfo",
        "category":"utility","download":"https://www.hwinfo.com/download/"},
    "malwarebytes|malware bytes": {
        "canonical":"Malwarebytes","winget":"Malwarebytes.Malwarebytes","choco":"malwarebytes",
        "category":"utility","download":"https://www.malwarebytes.com/mwb-download/thankyou/"},
    "crystaldiskinfo|crystal disk info": {
        "canonical":"CrystalDiskInfo","winget":"CrystalDewWorld.CrystalDiskInfo",
        "choco":"crystaldiskinfo","scoop":"extras/crystaldiskinfo",
        "category":"utility","download":"https://crystalmark.info/en/software/crystaldiskinfo/"},

    # ── Python libraries ──────────────────────────────────────────────────────
    "numpy|numpay|numphy|numppy": {"canonical":"NumPy","pip":"numpy","is_python_package":True,"category":"python_library","download":"https://pypi.org/project/numpy/"},
    "pandas|pndas|pandass": {"canonical":"Pandas","pip":"pandas","is_python_package":True,"category":"python_library","download":"https://pypi.org/project/pandas/"},
    "matplotlib|matplot lib|matplotlb": {"canonical":"Matplotlib","pip":"matplotlib","is_python_package":True,"category":"python_library","download":"https://pypi.org/project/matplotlib/"},
    "tensorflow|tensor flow|tensrflow": {"canonical":"TensorFlow","pip":"tensorflow","is_python_package":True,"category":"python_library","download":"https://pypi.org/project/tensorflow/"},
    "pytorch|torch|py torch": {"canonical":"PyTorch","pip":"torch","is_python_package":True,"category":"python_library","download":"https://pytorch.org/get-started/locally/"},
    "requests|request lib": {"canonical":"Requests","pip":"requests","is_python_package":True,"category":"python_library","download":"https://pypi.org/project/requests/"},
    "flask|flusk|flsk": {"canonical":"Flask","pip":"flask","is_python_package":True,"category":"python_library","download":"https://pypi.org/project/flask/"},
    "django|djnago|dajngo": {"canonical":"Django","pip":"django","is_python_package":True,"category":"python_library","download":"https://pypi.org/project/django/"},
    "fastapi|fast api": {"canonical":"FastAPI","pip":"fastapi","is_python_package":True,"category":"python_library","download":"https://pypi.org/project/fastapi/"},
    "scikit learn|sklearn|scikit-learn|scikitlearn": {"canonical":"scikit-learn","pip":"scikit-learn","is_python_package":True,"category":"python_library","download":"https://pypi.org/project/scikit-learn/"},
    "opencv|cv2|open cv": {"canonical":"OpenCV","pip":"opencv-python","is_python_package":True,"category":"python_library","download":"https://pypi.org/project/opencv-python/"},
    "pillow|pil|python imaging": {"canonical":"Pillow","pip":"Pillow","is_python_package":True,"category":"python_library","download":"https://pypi.org/project/Pillow/"},
    "sqlalchemy|sql alchemy": {"canonical":"SQLAlchemy","pip":"sqlalchemy","is_python_package":True,"category":"python_library","download":"https://pypi.org/project/SQLAlchemy/"},
    "langchain|lang chain": {"canonical":"LangChain","pip":"langchain","is_python_package":True,"category":"python_library","download":"https://pypi.org/project/langchain/"},
    "groq|groq api": {"canonical":"Groq Python SDK","pip":"groq","is_python_package":True,"category":"python_library","download":"https://pypi.org/project/groq/"},

    # ── Truly uninstallable ───────────────────────────────────────────────────
    "photoshop|adobe photoshop|photoshoop|phoshop": {
        "canonical":"Adobe Photoshop","is_uninstallable":True,
        "uninstallable_reason":"Requires a paid Adobe Creative Cloud subscription. Download from: https://www.adobe.com/products/photoshop.html",
        "download":"https://www.adobe.com/products/photoshop.html","category":"design"},
    "premiere pro|adobe premiere|premire pro": {
        "canonical":"Adobe Premiere Pro","is_uninstallable":True,
        "uninstallable_reason":"Requires paid Adobe Creative Cloud. Download from: https://www.adobe.com/products/premiere.html",
        "download":"https://www.adobe.com/products/premiere.html","category":"media"},
    "after effects|aftereffects": {
        "canonical":"Adobe After Effects","is_uninstallable":True,
        "uninstallable_reason":"Requires paid Adobe Creative Cloud. Download from: https://www.adobe.com/products/aftereffects.html",
        "download":"https://www.adobe.com/products/aftereffects.html","category":"media"},
    "illustrator|adobe illustrator": {
        "canonical":"Adobe Illustrator","is_uninstallable":True,
        "uninstallable_reason":"Requires paid Adobe Creative Cloud. Download from: https://www.adobe.com/products/illustrator.html",
        "download":"https://www.adobe.com/products/illustrator.html","category":"design"},
    "xcode|x code": {
        "canonical":"Xcode","is_uninstallable":True,"mas":"497799835",
        "uninstallable_reason":"macOS only — install from Mac App Store: https://apps.apple.com/us/app/xcode/id497799835",
        "download":"https://apps.apple.com/us/app/xcode/id497799835","category":"dev_tool"},
    "ms office|microsoft office|office 365": {
        "canonical":"Microsoft Office","is_uninstallable":True,
        "uninstallable_reason":"Requires Microsoft 365 subscription. Download from: https://www.microsoft.com/microsoft-365",
        "download":"https://www.microsoft.com/microsoft-365","category":"utility"},
    "final cut pro|final cut": {
        "canonical":"Final Cut Pro","is_uninstallable":True,
        "uninstallable_reason":"macOS only, paid. Get it from: https://www.apple.com/final-cut-pro/",
        "download":"https://www.apple.com/final-cut-pro/","category":"media"},
}

# Build flat alias → entry map at import time
_ALIAS_MAP: dict[str, dict] = {}
for _k, _v in _DB_RAW.items():
    for _alias in _k.split("|"):
        _ALIAS_MAP[_alias.strip().lower()] = _v


# ── Resolution helpers ─────────────────────────────────────────────────────────

def _local_resolve(raw: str) -> Optional[dict]:
    key = raw.strip().lower()
    if key in _ALIAS_MAP:
        return _ALIAS_MAP[key]
    matches = get_close_matches(key, list(_ALIAS_MAP.keys()), n=1, cutoff=0.72)
    if matches:
        log_step("✏️ ", f"Fuzzy matched '{raw}' → '{matches[0]}'")
        return _ALIAS_MAP[matches[0]]
    for alias, entry in _ALIAS_MAP.items():
        if key in alias or alias in key:
            log_step("✏️ ", f"Substring matched '{raw}' → '{alias}'")
            return entry
    return None


def _llm_resolve(raw: str) -> Optional[dict]:
    try:
        from llm_client import llm
        if not llm.is_groq_available():
            return None
    except Exception:
        return None

    prompt = f"""You are a software installation assistant.
The user wants to install: "{raw}"
Return ONLY valid JSON (no markdown):
{{"canonical_name":"","winget_id":"","msstore_id":"","choco_id":"","scoop_id":"","apt_id":"","snap_id":"","flatpak_id":"","dnf_id":"","pacman_id":"","brew_cask":"","brew_formula":"","mas_id":"","npm_id":"","pip_id":"","cargo_id":"","go_id":"","is_python_package":false,"is_uninstallable":false,"uninstallable_reason":"","category":"other","direct_download_url":""}}"""
    try:
        raw_resp = llm.chat(prompt, fast=False).strip()
        if "```" in raw_resp:
            raw_resp = raw_resp.split("```")[1].split("```")[0].replace("json","").strip()
        data = json.loads(raw_resp)
        return {
            "canonical": data.get("canonical_name", raw),
            "winget": data.get("winget_id",""), "msstore": data.get("msstore_id",""),
            "choco": data.get("choco_id",""), "scoop": data.get("scoop_id",""),
            "apt": data.get("apt_id",""), "snap": data.get("snap_id",""),
            "flatpak": data.get("flatpak_id",""), "dnf": data.get("dnf_id",""),
            "pacman": data.get("pacman_id",""), "brew_cask": data.get("brew_cask",""),
            "brew_formula": data.get("brew_formula",""), "mas": data.get("mas_id",""),
            "npm": data.get("npm_id",""), "pip": data.get("pip_id",""),
            "cargo": data.get("cargo_id",""), "go": data.get("go_id",""),
            "is_python_package": data.get("is_python_package", False),
            "is_uninstallable": data.get("is_uninstallable", False),
            "uninstallable_reason": data.get("uninstallable_reason",""),
            "category": data.get("category","other"),
            "download": data.get("direct_download_url",""),
        }
    except Exception as exc:
        logger.warning(f"LLM resolve failed: {exc}")
        return None


def _pypi_search(query: str) -> Optional[str]:
    """Search PyPI and return the best matching package name."""
    try:
        url = f"https://pypi.org/pypi/{urllib.parse.quote(query)}/json"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
            return data["info"]["name"]
    except Exception:
        pass
    # Try search API
    try:
        url = f"https://pypi.org/search/?q={urllib.parse.quote(query)}&format=json"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            return None  # just signals it exists
    except Exception:
        return None


def _winget_search_live(query: str) -> Optional[str]:
    rc, out, _ = _run(
        ["winget","search",query,"--accept-source-agreements","--limit","5"], timeout=30)
    if rc != 0:
        return None
    header_passed = False
    for line in out.splitlines():
        if re.match(r"^[-\s]+$", line):
            header_passed = True
            continue
        if header_passed and line.strip():
            for part in line.split():
                if "." in part and len(part) > 3:
                    return part
    return None


def _apt_search_live(query: str) -> Optional[str]:
    rc, out, _ = _run(["apt-cache","search","--names-only", query], timeout=15)
    if rc == 0 and out.strip():
        return out.splitlines()[0].split()[0]
    rc, out, _ = _run(["apt-cache","search", query], timeout=15)
    if rc == 0 and out.strip():
        return out.splitlines()[0].split(" - ")[0].strip()
    return None


def _brew_search_live(query: str) -> Optional[tuple]:
    rc, out, _ = _run(["brew","search", query], timeout=20)
    if rc != 0 or not out.strip():
        return None
    casks, formulae = [], []
    in_casks = False
    for line in out.splitlines():
        line = line.strip()
        if not line: continue
        if "Casks" in line:
            in_casks = True; continue
        if "Formulae" in line:
            in_casks = False; continue
        (casks if in_casks else formulae).append(line)
    if casks:   return casks[0], True
    if formulae: return formulae[0], False
    return None


# ── Subprocess runner ──────────────────────────────────────────────────────────

def _run(cmd: list, timeout: int = 300) -> tuple:
    print(f"    $ {' '.join(str(c) for c in cmd)}")
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace")
        lines = []
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                print(f"    {line}")
                lines.append(line)
        proc.wait(timeout=timeout)
        return proc.returncode, "\n".join(lines), proc.stderr.read()
    except subprocess.TimeoutExpired:
        proc.kill(); return -1, "", f"Timed out"
    except FileNotFoundError:
        return -1, "", f"Command not found: {cmd[0]}"
    except Exception as exc:
        return -1, "", str(exc)


def _cmd_exists(cmd: str) -> bool:
    return shutil.which(cmd) is not None


# ── Result types ───────────────────────────────────────────────────────────────

@dataclass
class InstallResult:
    success: bool; app_name: str; resolved_name: str
    package_id: str; method: str; message: str
    already_installed: bool = False; output: str = ""; download_url: str = ""
    def to_dict(self): return self.__dict__

def _ok(a,c,p,m,o=""):  return InstallResult(True,a,c,p,m,f"✅ Installed '{c}' via {m}.",output=o)
def _already(a,c):       return InstallResult(True,a,c,c,"already_installed",f"✅ '{c}' is already installed.",already_installed=True)
def _cant(a,c,reason,url=""): return InstallResult(False,a,c,"","none",f"⚠️  '{c}' cannot be auto-installed.\n   {reason}",download_url=url)
def _fail(a,c,tried,url=""):
    search={"Windows":f"https://winstall.app/search?q={urllib.parse.quote(c)}",
            "Linux":  f"https://repology.org/projects/?search={urllib.parse.quote(c)}",
            "Darwin": f"https://formulae.brew.sh/?q={urllib.parse.quote(c)}"}
    u = url or search.get(OS, f"https://google.com/search?q=install+{urllib.parse.quote(c)}")
    return InstallResult(False,a,c,"","none",
        f"⚠️  Could not auto-install '{c}' (tried: {tried}).\n"
        f"   👉 Download manually: {u}",download_url=u)


# ── Already installed ──────────────────────────────────────────────────────────

def is_installed(canonical: str, pip_id: str = "") -> bool:
    key = canonical.lower()
    bins = {"git":"git","node":"node","nodejs":"node","python":"python3","python 3":"python3",
            "postgresql":"psql","mysql":"mysql","redis":"redis-cli","docker":"docker",
            "vim":"vim","neovim":"nvim","curl":"curl","wget":"wget","ffmpeg":"ffmpeg",
            "ollama":"ollama","go":"go","rust":"rustc","ruby":"ruby","php":"php",
            "visual studio code":"code","gh":"gh","kubectl":"kubectl"}
    for k,b in bins.items():
        if k in key and _cmd_exists(b): return True
    if pip_id:
        rc,_,_ = _run([sys.executable,"-m","pip","show",pip_id],timeout=10)
        if rc==0: return True
    if OS=="Windows" and _cmd_exists("winget"):
        rc,out,_ = _run(["winget","list","--name",canonical,"--accept-source-agreements"],timeout=30)
        if rc==0 and canonical.lower() in out.lower(): return True
    return False


# ══════════════════════════════════════════════════════════════════════════════
# INSTALL CHAINS
# ══════════════════════════════════════════════════════════════════════════════

def _install_windows(app: str, e: dict) -> InstallResult:
    c=e.get("canonical",app); pip_id=e.get("pip",""); is_py=e.get("is_python_package",False)
    if e.get("is_uninstallable"): return _cant(app,c,e.get("uninstallable_reason",""),e.get("download",""))
    if is_installed(c,pip_id): return _already(app,c)
    tried=[]

    if is_py and pip_id:
        log_step("🐍",f"[pip] {pip_id}")
        rc,out,_ = _run([sys.executable,"-m","pip","install",pip_id])
        if rc==0: return _ok(app,c,pip_id,"pip",out)
        tried.append("pip")

    if _cmd_exists("winget"):
        pkg = e.get("winget","") or _winget_search_live(c) or c
        log_step("📦",f"[winget] {pkg}")
        rc,out,err = _run(["winget","install","--id",pkg,"-e","--accept-package-agreements","--accept-source-agreements"])
        if rc==0 or "already installed" in (out+err).lower(): return _ok(app,c,pkg,"winget",out)
        found = _winget_search_live(c)
        if found and found!=pkg:
            log_step("🔍",f"[winget live search] {found}")
            rc,out,_ = _run(["winget","install","--id",found,"-e","--accept-package-agreements","--accept-source-agreements"])
            if rc==0: return _ok(app,c,found,"winget",out)
        tried.append("winget")

    if _cmd_exists("winget"):
        sid=e.get("msstore","")
        if sid:
            log_step("🏪",f"[MS Store] {sid}")
            rc,out,_ = _run(["winget","install","--id",sid,"--source","msstore","--accept-package-agreements","--accept-source-agreements"])
            if rc==0: return _ok(app,c,sid,"Microsoft Store",out)
        tried.append("msstore")

    if _cmd_exists("choco"):
        cid=e.get("choco","") or c.lower().replace(" ","-")
        log_step("🍫",f"[choco] {cid}")
        rc,out,_ = _run(["choco","install",cid,"-y"])
        if rc==0: return _ok(app,c,cid,"chocolatey",out)
        tried.append("choco")

    if _cmd_exists("scoop"):
        sid=e.get("scoop","") or c.lower().replace(" ","-")
        _run(["scoop","bucket","add","extras"],timeout=30)
        log_step("🥄",f"[scoop] {sid}")
        rc,out,_ = _run(["scoop","install",sid])
        if rc==0: return _ok(app,c,sid,"scoop",out)
        tried.append("scoop")

    # npm, cargo, go
    for key_,cmd_ in [("npm",["npm","install","-g"]),("cargo",["cargo","install"]),("go",["go","install"])]:
        vid=e.get(key_,"")
        if vid and _cmd_exists(cmd_[0]):
            pkg_=f"{vid}@latest" if key_=="go" and "@" not in vid else vid
            log_step("📦",f"[{key_}] {pkg_}")
            rc,out,_ = _run(cmd_+[pkg_])
            if rc==0: return _ok(app,c,pkg_,key_,out)
            tried.append(key_)

    if pip_id and not is_py:
        rc,out,_ = _run([sys.executable,"-m","pip","install",pip_id])
        if rc==0: return _ok(app,c,pip_id,"pip",out)
        tried.append("pip")

    return _fail(app,c,"/".join(tried),e.get("download",""))


def _install_linux(app: str, e: dict) -> InstallResult:
    c=e.get("canonical",app); pip_id=e.get("pip",""); is_py=e.get("is_python_package",False)
    if e.get("is_uninstallable"): return _cant(app,c,e.get("uninstallable_reason",""),e.get("download",""))
    if is_installed(c,pip_id): return _already(app,c)
    tried=[]

    if is_py and pip_id:
        rc,out,_ = _run([sys.executable,"-m","pip","install",pip_id])
        if rc==0: return _ok(app,c,pip_id,"pip",out)
        tried.append("pip")

    for mgr in (["apt"],["apt-get"]):
        if _cmd_exists(mgr[0]):
            aid=e.get("apt","") or _apt_search_live(c) or c.lower().replace(" ","-")
            _run(["sudo",mgr[0],"update","-qq"],timeout=60)
            log_step("📦",f"[{mgr[0]}] {aid}")
            rc,out,_ = _run(["sudo",mgr[0],"install","-y",aid],timeout=300)
            if rc==0: return _ok(app,c,aid,mgr[0],out)
            tried.append(mgr[0]); break

    if _cmd_exists("snap"):
        raw_=e.get("snap","") or c.lower().replace(" ","-")
        parts_=raw_.split(); sid_=parts_[0]; flags_=parts_[1:]
        for cmd_ in (["sudo","snap","install",sid_]+flags_,["sudo","snap","install",sid_,"--classic"]):
            log_step("📦",f"[snap] {' '.join(cmd_[3:])}")
            rc,out,_ = _run(cmd_,timeout=300)
            if rc==0: return _ok(app,c,sid_,"snap",out)
        tried.append("snap")

    if _cmd_exists("flatpak"):
        fid=e.get("flatpak","") or c.lower().replace(" ",".")
        _run(["flatpak","remote-add","--if-not-exists","flathub","https://flathub.org/repo/flathub.flatpakrepo"],timeout=30)
        log_step("📦",f"[flatpak] {fid}")
        rc,out,_ = _run(["flatpak","install","-y","flathub",fid],timeout=300)
        if rc==0: return _ok(app,c,fid,"flatpak",out)
        # flatpak search fallback
        rc2,out2,_ = _run(["flatpak","search",c.lower()],timeout=20)
        if rc2==0 and out2.strip():
            for ln in out2.splitlines()[1:]:
                ps=ln.split(); 
                if len(ps)>=2:
                    fid2=ps[-1]
                    rc3,out3,_ = _run(["flatpak","install","-y","flathub",fid2],timeout=300)
                    if rc3==0: return _ok(app,c,fid2,"flatpak",out3)
                    break
        tried.append("flatpak")

    for mgr in (["dnf"],["yum"]):
        if _cmd_exists(mgr[0]):
            did=e.get("dnf","") or c.lower().replace(" ","-")
            rc,out,_ = _run(["sudo",mgr[0],"install","-y",did],timeout=300)
            if rc==0: return _ok(app,c,did,mgr[0],out)
            tried.append(mgr[0]); break

    for mgr in (["yay"],["pacman"]):
        if _cmd_exists(mgr[0]):
            pid=e.get("pacman","") or c.lower().replace(" ","-")
            cmd_=([mgr[0],"-S","--noconfirm",pid] if mgr[0]=="yay" else ["sudo","pacman","-S","--noconfirm",pid])
            rc,out,_ = _run(cmd_,timeout=300)
            if rc==0: return _ok(app,c,pid,mgr[0],out)
            tried.append(mgr[0]); break

    if _cmd_exists("zypper"):
        zid=c.lower().replace(" ","-")
        rc,out,_ = _run(["sudo","zypper","install","-y",zid],timeout=300)
        if rc==0: return _ok(app,c,zid,"zypper",out)
        tried.append("zypper")

    for key_,cmd_ in [("npm",["npm","install","-g"]),("cargo",["cargo","install"]),("go",["go","install"])]:
        vid=e.get(key_,"")
        if vid and _cmd_exists(cmd_[0]):
            pkg_=f"{vid}@latest" if key_=="go" and "@" not in vid else vid
            rc,out,_ = _run(cmd_+[pkg_])
            if rc==0: return _ok(app,c,pkg_,key_,out)
            tried.append(key_)

    if pip_id and not is_py:
        rc,out,_ = _run([sys.executable,"-m","pip","install",pip_id])
        if rc==0: return _ok(app,c,pip_id,"pip",out)
        tried.append("pip")

    return _fail(app,c,"/".join(tried),e.get("download",""))


def _install_macos(app: str, e: dict) -> InstallResult:
    c=e.get("canonical",app); pip_id=e.get("pip",""); is_py=e.get("is_python_package",False)
    if e.get("is_uninstallable"):
        if e.get("mas") and _cmd_exists("mas"):
            rc,out,_ = _run(["mas","install",e["mas"]])
            if rc==0: return _ok(app,c,e["mas"],"Mac App Store",out)
        return _cant(app,c,e.get("uninstallable_reason",""),e.get("download",""))
    if is_installed(c,pip_id): return _already(app,c)
    tried=[]

    if is_py and pip_id:
        rc,out,_ = _run([sys.executable,"-m","pip","install",pip_id])
        if rc==0: return _ok(app,c,pip_id,"pip",out)
        tried.append("pip")

    if _cmd_exists("brew"):
        for kind, field_ in (("cask","brew_cask"),("formula","brew_formula")):
            pkg=e.get(field_,"")
            if not pkg:
                res=_brew_search_live(c)
                if res: pkg=res[0]
            if pkg:
                cmd_=["brew","install","--cask",pkg] if kind=="cask" else ["brew","install",pkg]
                log_step("🍺",f"[brew {kind}] {pkg}")
                rc,out,_ = _run(cmd_,timeout=300)
                if rc==0: return _ok(app,c,pkg,f"brew {kind}",out)
            tried.append(f"brew {kind}")

    if _cmd_exists("mas"):
        mid=e.get("mas","")
        if mid:
            rc,out,_ = _run(["mas","install",mid])
            if rc==0: return _ok(app,c,mid,"Mac App Store",out)
        tried.append("mas")

    for key_,cmd_ in [("npm",["npm","install","-g"]),("cargo",["cargo","install"]),("go",["go","install"])]:
        vid=e.get(key_,"")
        if vid and _cmd_exists(cmd_[0]):
            pkg_=f"{vid}@latest" if key_=="go" and "@" not in vid else vid
            rc,out,_ = _run(cmd_+[pkg_])
            if rc==0: return _ok(app,c,pkg_,key_,out)
            tried.append(key_)

    if pip_id and not is_py:
        rc,out,_ = _run([sys.executable,"-m","pip","install",pip_id])
        if rc==0: return _ok(app,c,pip_id,"pip",out)
        tried.append("pip")

    return _fail(app,c,"/".join(tried),e.get("download",""))


# ── Public API ─────────────────────────────────────────────────────────────────

def install_software(app_name: str) -> dict:
    """
    Install any software. Handles typos, slang, abbreviations.
    Works fully offline via local DB + fuzzy match.
    LLM used as enhancement when available.
    Live package search as final safety net.
    """
    print(f"\n  📦 Resolving: '{app_name}'  (OS: {OS})")

    # Layer 1: local DB + fuzzy
    entry = _local_resolve(app_name)
    if entry:
        log_step("✅", f"DB match: '{app_name}' → '{entry.get('canonical')}'")
    else:
        # Layer 2: LLM (if available)
        entry = _llm_resolve(app_name)
        if entry:
            log_step("✅", f"LLM resolved: '{app_name}' → '{entry.get('canonical')}'")
        else:
            # Layer 3: build a best-effort entry using live search
            log_step("🔍", f"Not in DB — attempting live search for '{app_name}'…")
            slug = app_name.lower().replace(" ", "-")

            # Check PyPI first (it's fast and catches Python libs)
            pypi_name = _pypi_search(app_name)

            # Try winget live search to get a real package ID
            winget_id = ""
            if _cmd_exists("winget"):
                winget_id = _winget_search_live(app_name) or ""

            if pypi_name and not winget_id:
                log_step("🐍", f"Found on PyPI: {pypi_name}")
                entry = {"canonical": app_name, "pip": pypi_name,
                         "is_python_package": True, "is_uninstallable": False, "download": f"https://pypi.org/project/{pypi_name}/"}
            elif winget_id:
                log_step("📦", f"Found on winget: {winget_id}")
                entry = {"canonical": app_name, "winget": winget_id,
                         "is_python_package": False, "is_uninstallable": False, "download": ""}
            else:
                # Truly unknown — use slug and let install chains try everything
                entry = {"canonical": app_name, "winget": slug, "choco": slug,
                         "apt": slug, "snap": slug, "brew_cask": slug, "brew_formula": slug,
                         "pip": slug, "is_python_package": False, "is_uninstallable": False,
                         "download": f"https://google.com/search?q=download+{urllib.parse.quote(app_name)}"}

    if OS == "Windows":  result = _install_windows(app_name, entry)
    elif OS == "Linux":  result = _install_linux(app_name, entry)
    elif OS == "Darwin": result = _install_macos(app_name, entry)
    else: result = InstallResult(False,app_name,entry.get("canonical",app_name),"","none",f"❌ Unsupported OS: {OS}",download_url=entry.get("download",""))

    print(f"\n  {'✅' if result.success else '⚠️ '} {result.message}")
    return result.to_dict()


def search_package(app_name: str) -> list[str]:
    results = []
    if OS == "Windows" and _cmd_exists("winget"):
        rc,out,_ = _run(["winget","search",app_name,"--accept-source-agreements"],timeout=30)
        if rc==0:
            header_passed=False
            for line in out.splitlines():
                if re.match(r"^[-\s]+$",line): header_passed=True; continue
                if header_passed and line.strip():
                    parts=line.split()
                    if len(parts)>=2: results.append(parts[1])
    elif OS=="Linux" and _cmd_exists("apt-cache"):
        rc,out,_ = _run(["apt-cache","search",app_name],timeout=20)
        if rc==0:
            for line in out.splitlines(): results.append(line.split(" - ")[0].strip())
    elif OS=="Darwin" and _cmd_exists("brew"):
        rc,out,_ = _run(["brew","search",app_name],timeout=20)
        if rc==0: results=[l.strip() for l in out.splitlines() if l.strip() and not l.startswith("=")]
    return results[:10]
