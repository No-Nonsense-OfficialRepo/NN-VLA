pragma Singleton
import QtQuick
QtObject {
    readonly property var names: ({
        "library":"grid", "room and players":"network", "downloads":"downloads",
        "activity":"activity", "profile":"user", "player":"user", "events":"activity", "settings":"settings",
        "game library":"storage", "network":"network-settings", "appearance":"palette",
        "diagnostics":"info", "all games":"filter-all", "installed":"filter-installed",
        "playing":"filter-playing", "clear search & filters":"clear-filter",
        "prepare vla":"prepare-vla", "install / retry vla":"prepare-vla",
        "find host":"search", "connect by ip":"connect", "join room key":"key",
        "repair lan":"repair", "repair lan access":"repair", "wipe vla":"trash",
        "copy room id":"copy-key", "copy lan ip":"copy-ip", "copy":"copy",
        "copy diagnostic summary":"clipboard", "open room":"room-start",
        "close room":"room-stop", "stop hosting":"room-stop", "leave room":"room-leave",
        "install":"download", "play":"play", "pause download":"pause",
        "resume download":"resume", "game settings":"sliders", "open folder":"folder-open",
        "add game":"add", "add game folder":"add", "rescan folders":"refresh",
        "choose game artwork":"image", "choose cover art":"image",
        "cover art selected":"image", "choose .exe":"terminal", ".exe":"terminal",
        "change directory":"folder", "browse":"folder", "browse folder":"folder",
        "save changes":"save", "save profile":"save-profile", "save game":"save-game",
        "save launch settings":"save-launch", "save setup":"setup-complete",
        "theme and resolution settings":"palette", "cancel":"cancel",
        "close message":"close", "close application":"close", "close":"close",
        "minimize window":"minimize", "more":"more",
        "back":"back", "back to game":"back", "choose bundle":"share",
        "import bundle":"share", "share bundle":"share",
        "clean up this pc":"clean-pc", "save join pack":"save-pack", "open join pack":"open-pack"
    })
    function forLabel(label) { return names[String(label).toLowerCase()] || ""; }
}
