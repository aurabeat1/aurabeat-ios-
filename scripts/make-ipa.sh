#!/bin/zsh
# Build a Release IPA for testers to sideload (AltStore, SideStore, Sideloadly, …): the app and its
# two extensions, ad-hoc signed with their entitlements so the sideloader re-signs them with the
# tester's own Apple ID and remaps the shared app group. No Apple Developer account is needed here.
#   scripts/make-ipa.sh            → build/AuraBeat-<version>-<build>.ipa
# JOBS=n caps parallel compile jobs (default 3), as in the other scripts.
set -euo pipefail
if [ -z "${DEVELOPER_DIR:-}" ]; then
  if [ -d "/Applications/Xcode.app/Contents/Developer" ]; then
    export DEVELOPER_DIR="/Applications/Xcode.app/Contents/Developer"
  else
    export DEVELOPER_DIR="$(xcode-select -p 2>/dev/null || echo '/Applications/Xcode.app/Contents/Developer')"
  fi
fi
cd "$(dirname "$0")/.."

DERIVED=.derived-release
xcodebuild build -workspace AuraBeat.xcworkspace -scheme AuraBeat -configuration Release \
  -destination 'generic/platform=iOS' -derivedDataPath "$DERIVED" -jobs "${JOBS:-3}" -quiet \
  CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY=""

APP="$DERIVED/Build/Products/Release-iphoneos/AuraBeat.app"
[ -d "$APP" ] || { echo "no app at $APP" >&2; exit 1; }
VERSION=$(/usr/libexec/PlistBuddy -c "Print CFBundleShortVersionString" "$APP/Info.plist")
BUILD=$(/usr/libexec/PlistBuddy -c "Print CFBundleVersion" "$APP/Info.plist")

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/Payload"
cp -R "$APP" "$STAGE/Payload/"
STAGED="$STAGE/Payload/AuraBeat.app"

# Inside out: embedded frameworks, then each extension with its entitlements, then the app.
find "$STAGED" -name "*.framework" -type d -prune | while read -r fw; do
  codesign --force --sign - --timestamp=none "$fw"
done
codesign --force --sign - --timestamp=none --entitlements App/Config/AuraBeatWidget.entitlements \
  "$STAGED/PlugIns/AuraBeatWidgetExtension.appex"
codesign --force --sign - --timestamp=none --entitlements App/Config/AuraBeatShare.entitlements \
  "$STAGED/PlugIns/AuraBeatShareExtension.appex"
codesign --force --sign - --timestamp=none --entitlements App/Config/AuraBeat.entitlements "$STAGED"

mkdir -p build
OUT="build/AuraBeat-$VERSION-$BUILD.ipa"
rm -f "$OUT"
(cd "$STAGE" && zip -qry - Payload) > "$OUT"
echo "$OUT ($(du -h "$OUT" | cut -f1))"
scripts/verify-ipa.sh "$OUT"
