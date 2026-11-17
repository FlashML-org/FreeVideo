import QtQuick
import QtMultimedia

// A release video for FReleaseMedia, loaded only where Qt Multimedia is present: it loops and starts muted.
Item {
    id: video
    property url source
    property bool muted: true
    readonly property bool playing: player.playbackState === MediaPlayer.PlayingState && player.position > 0
    MediaPlayer {
        id: player
        source: video.source
        loops: MediaPlayer.Infinite
        videoOutput: output
        audioOutput: AudioOutput { muted: video.muted }
        // Asked to play while still loading, the player stays loading; it starts once the file is open.
        onMediaStatusChanged: if (mediaStatus === MediaPlayer.LoadedMedia) play()
    }
    VideoOutput { id: output; anchors.fill: parent; fillMode: VideoOutput.PreserveAspectCrop }
    Component.onDestruction: player.stop()
}
