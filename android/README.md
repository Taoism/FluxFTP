# FluxFTP Android companion

The companion is one adaptive Android app for phone and tablet screens. It
connects to the desktop's authenticated JSON API, displays transfer status,
starts/pauses/stops queue work, cancels individual transfers, and submits a
transfer between desktop-local paths and saved FluxFTP sites.

## Build

Open this `android` directory in Android Studio, or run:

```powershell
./gradlew.bat assembleDebug
```

The debug APK is written to `app/build/outputs/apk/debug/app-debug.apk`.
The first Gradle run downloads the pinned Gradle and Android Gradle Plugin
versions. Android Studio's bundled JDK is suitable for the build.

## Connect to FluxFTP

FluxFTP must be running, and the JSON API token is available under **Tools →
JSON API Access**. The client encrypts the token with an Android Keystore AES
key. The API itself must stay bound to `127.0.0.1:8765`; never expose it on a
LAN or the public internet.

For an Android emulator, run this on the development computer before opening
the app:

```powershell
& "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe" reverse tcp:8765 tcp:8765
```

Then set the app endpoint to `http://127.0.0.1:8765` and enter the API token.
On a phone, connect through an authenticated SSH local forward or a private VPN
HTTPS proxy to the desktop's loopback API. The client rejects plain HTTP for
non-loopback endpoints.

Transfer paths are paths on the **FluxFTP desktop**, not paths on the Android
device. The current desktop API does not yet offer file browsing or direct
transfer of Android-local files; those require further API and client work.
