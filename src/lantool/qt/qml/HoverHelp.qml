pragma Singleton
import QtQuick
QtObject {
 id: help
 property var currentOwner: null
 property string pendingText: ""
 property string description: ""
 function begin(owner, message) {
  if(!message) return;
  if(currentOwner===owner && pendingText===message) return;
  currentOwner=owner; pendingText=message; description=""; dwell.restart();
 }
 function end(owner) { if(currentOwner===owner) clear(); }
 function clear() { dwell.stop(); currentOwner=null; pendingText=""; description=""; }
 function describe(label) {
  let descriptions={
   "Library":"Browse your shared game library.",
   "Downloads":"View installation progress and pause or resume a download.",
   "Prepare VLA":"Install or restart the room network service.",
   "Find host":"Search for a Host on your LAN.",
   "Install":"Download the selected game or shared folder without launching it.",
   "Play":"Launch the selected installed game.",
   "Pause download":"Pause the current installation.",
   "Resume download":"Continue a paused installation.",
   "Save setup":"Save your player name, game directory and installation preference.",
   "Save launch settings":"Save launch arguments for the selected game.",
   "Leave Room":"Disconnect this Client from the active room.",
   "Room and players":"View the room, connected players and local network.",
   "Profile":"Change your player name.",
   "Settings":"Configure the launcher and network settings.",
   "All games":"Show all games in the shared library.",
   "Installed":"Show games installed on this computer.",
   "Playing":"Show games currently being played in the room.",
   "Install / Retry VLA":"Install or restart VLA (Virtual LAN Adaptor), then search for your host.",
   "Wipe VLA":"Reset launcher data and uninstall launcher-owned VLA. Keep themes, resolutions and games.",
   "Repair Network/Wipe VLA":"Reset launcher data and uninstall launcher-owned VLA. Keep themes, resolutions and games.",
   "Network Diagnostic":"Check this PC's network and show fixes for detected issues.",
   "Connect by IP":"Connect to the LAN host at the entered IP address.",
   "Repair LAN":"Repair local network access rules.",
   "Repair LAN access":"Repair local network access rules.",
   "Activity":"View recent room events.",
   "View room":"Open the connected room and player list.",
   "Find your host":"Open room discovery and connection controls.",
   "Open Room":"Start hosting a LAN room.",
   "Close Room":"Close this room after confirmation.",
   "Share join bundle":"Export a signed bundle so another player can join.",
   "Copy LAN IP":"Copy the host's local IP address.",
   "Copy room ID":"Copy the room identifier.",
   "Add game":"Choose a game folder to share.",
   "Rescan folders":"Refresh the shared game directory.",
   "Choose game artwork":"Choose cover art for this game.",
   ".exe":"Choose this game's executable.",
   "Change directory":"Choose where game files are stored. Leave the room first.",
   "Close application":"Close the launcher and clean up the connection.",
   "Minimize window":"Minimize the launcher.",
   "Open Library":"Browse the shared game library.",
   "Manage Games":"Edit the shared game catalog.",
   "Game Settings":"Change this game's local launch arguments.",
   "Save changes":"Save the edited settings on this computer.",
   "General":"General launcher settings.",
   "Game Library":"Game directory and shared library settings.",
   "Network":"Local discovery and host connection settings.",
   "Appearance":"View the current visual theme.",
   "Diagnostics":"View connection details and copy a diagnostic summary."
  };
  return descriptions[label] || label;
 }
 property Timer dwell: Timer {
  interval: 800; repeat: false
  onTriggered: { if(help.currentOwner) help.description=help.pendingText; }
 }
}
