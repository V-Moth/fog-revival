# fog-revival
Project aimed at resurrecting the Steam PC version of Forge Of Gods by Panoramik. The revival is done via modification of the source files from the last published version of the game on Steam and through the creation of a server emulator. The modified Assembly-CSharp.dll is required as I routed all server calls to 127.0.0.1.

The game is currently unplayable and will not go past the initial loading screen due to missing config files, which I am trying to get the game to make on it's own. Contributions to the project are more than welcome.

Requirements:
- Python 3.13
- Forge Of Gods PC Steam (latest version)

Installation:
1. Install Python 3.13
2. Download Forge Of Gods PC Steam version
3. Extract the modified Assembly-CSharp.dll and replace when asked, to X:\SteamLibrary\steamapps\common\Forge of Gods (RPG)\fog_Data\Managed (X being your drive where the game is installed)
4. Extract the server.py file to a folder
5. Modify the server.py file's STEAM_BASE64 with your own Steam account ID.
6. Copy everything from fog_Data to configs folder
7. Copy all resource asset files (*.resource, *.ress, *.asset etc) from fog_Data to static folder.


How to run:
- Open server.py in IDLE and click on Run Module or press F5
- Start the game from Steam by clicking on Play
  

TODO
- Get it to actually load into the game
- Player Profile creation
- Restore all primary functions (Gameplay - single-player etc)
- Restore "online" functions (DLC availability, login rewards etc)
- Remove Steam client dependency
