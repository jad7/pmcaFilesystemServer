-include .env

JAVA8_HOME ?= $(JAVA_HOME)
ANDROID_SDK_ROOT ?= $(ANDROID_HOME)
ADB ?= adb
ADB_TARGET ?=
APP_PACKAGE := info.schnatterer.pmcaFilesystemServer
APK_DEBUG := app/build/outputs/apk/debug/app-debug.apk
APK_RELEASE_SIGNED := app/build/outputs/apk/releaseSigned/app-releaseSigned.apk
PYTHON ?= python3
CAMERA_BASE_URL ?=
SYNC_DEST_DIR ?=
SYNC_CONFIG ?= config/sony_a6000_sync.json
SYNC_BUNDLE ?= build/sony-a6000-sync.pyz
PROBE_ARGS ?=
SYNC_ARGS ?=
GRADLEW := bash ./gradlew
GRADLE_ENV := JAVA_HOME="$(JAVA8_HOME)" ANDROID_SDK_ROOT="$(ANDROID_SDK_ROOT)" ANDROID_HOME="$(ANDROID_SDK_ROOT)"

.PHONY: help doctor build build-no-tests test test-unit apk apk-debug apk-release apk-release-signed clean \
	adb-connect adb-packages adb-install-debug adb-install-release-signed adb-uninstall \
	adb-reinstall-debug adb-reinstall-release-signed camera-probe camera-sync sony-a6000-sync sony-a6000-sync-bundle

help:
	@printf '%s\n' \
		'Targets:' \
		'  make build          - Gradle build with tests' \
		'  make build-no-tests  - Debug assemble without running tests' \
		'  make test            - Run all unit tests' \
		'  make test-unit       - Run debug unit tests only' \
		'  make apk             - Build debug APK' \
		'  make apk-debug       - Build debug APK' \
		'  make apk-release     - Build release APK' \
		'  make apk-release-signed - Build signed release APK for installation' \
		'  make adb-connect     - Connect to a device over adb' \
		'  make adb-packages    - List installed packages on the device' \
		'  make adb-install-debug - Install the debug APK with adb -t' \
		'  make adb-install-release-signed - Install the signed release APK with adb -t' \
		'  make adb-uninstall   - Remove the app from the device' \
		'  make adb-reinstall-debug - Uninstall and reinstall the debug APK' \
		'  make adb-reinstall-release-signed - Uninstall and reinstall the signed release APK' \
		'  make camera-probe    - Run the live camera probe script' \
		'  make camera-sync     - Download files from camera into a local directory' \
		'  make sony-a6000-sync - Sync camera media into Immich using the singleton cursor' \
		'  make sony-a6000-sync-bundle - Build a single-file zipapp bundle' \
		'  make clean           - Clean build outputs' \
		'  make doctor          - Print configured paths'

doctor:
	@printf 'JAVA8_HOME=%s\nANDROID_SDK_ROOT=%s\nANDROID_HOME=%s\nADB=%s\nADB_TARGET=%s\nCAMERA_BASE_URL=%s\nPYTHON=%s\n' \
		"$(JAVA8_HOME)" "$(ANDROID_SDK_ROOT)" "$(ANDROID_SDK_ROOT)" "$(ADB)" "$(ADB_TARGET)" "$(CAMERA_BASE_URL)" "$(PYTHON)"

build:
	@$(GRADLE_ENV) $(GRADLEW) build

build-no-tests:
	@$(GRADLE_ENV) $(GRADLEW) assembleDebug

test:
	@$(GRADLE_ENV) $(GRADLEW) test

test-unit:
	@$(GRADLE_ENV) $(GRADLEW) testDebugUnitTest

apk: apk-debug

apk-debug:
	@$(GRADLE_ENV) $(GRADLEW) assembleDebug

apk-release:
	@$(GRADLE_ENV) $(GRADLEW) assembleRelease

apk-release-signed:
	@$(GRADLE_ENV) $(GRADLEW) assembleReleaseSigned

adb-connect:
	@[ -n "$(ADB_TARGET)" ] || { echo 'Set ADB_TARGET=ip:port'; exit 1; }
	@$(ADB) connect "$(ADB_TARGET)"

adb-packages:
	@$(ADB) shell pm list packages

adb-install-debug:
	@$(ADB) install -t "$(APK_DEBUG)"

adb-install-release-signed:
	@$(ADB) install -t "$(APK_RELEASE_SIGNED)"

adb-uninstall:
	@$(ADB) uninstall "$(APP_PACKAGE)"

adb-reinstall-debug:
	@$(ADB) uninstall "$(APP_PACKAGE)" || true
	@$(ADB) install -t "$(APK_DEBUG)"

adb-reinstall-release-signed:
	@$(ADB) uninstall "$(APP_PACKAGE)" || true
	@$(ADB) install -t "$(APK_RELEASE_SIGNED)"

camera-probe:
	@[ -n "$(CAMERA_BASE_URL)" ] || { echo 'Set CAMERA_BASE_URL=http://camera-ip:8080'; exit 1; }
	@$(PYTHON) tools/live_camera_probe.py --base-url "$(CAMERA_BASE_URL)" $(PROBE_ARGS)

camera-sync:
	@[ -n "$(CAMERA_BASE_URL)" ] || { echo 'Set CAMERA_BASE_URL=http://camera-ip:8080'; exit 1; }
	@[ -n "$(SYNC_DEST_DIR)" ] || { echo 'Set SYNC_DEST_DIR=/path/to/destination'; exit 1; }
	@$(PYTHON) tools/camera_sync.py --base-url "$(CAMERA_BASE_URL)" --dest-dir "$(SYNC_DEST_DIR)" $(SYNC_ARGS)

sony-a6000-sync:
	@[ -f "$(SYNC_CONFIG)" ] || { echo 'Set SYNC_CONFIG=config/sony_a6000_sync.json and copy the example config first'; exit 1; }
	@$(PYTHON) tools/sony_a6000_sync.py --config "$(SYNC_CONFIG)" $(SYNC_ARGS)

sony-a6000-sync-bundle:
	@rm -rf build/sony-a6000-sync-bundle
	@mkdir -p build/sony-a6000-sync-bundle
	@cp tools/camera_client.py build/sony-a6000-sync-bundle/camera_client.py
	@cp tools/sync_store.py build/sony-a6000-sync-bundle/sync_store.py
	@cp tools/sony_a6000_sync.py build/sony-a6000-sync-bundle/sony_a6000_sync.py
	@printf '%s\n' 'from sony_a6000_sync import main' 'import sys' 'sys.exit(main())' > build/sony-a6000-sync-bundle/__main__.py
	@$(PYTHON) -m zipapp build/sony-a6000-sync-bundle -o "$(SYNC_BUNDLE)"

clean:
	@$(GRADLE_ENV) $(GRADLEW) clean

.PHONY: test-sync test-transfer
test-sync:
	@PYTHONPATH=tools $(PYTHON) -m unittest discover -s tests -v

test-transfer:
	@mkdir -p build/transfer-tests
	@"$(JAVA8_HOME)/bin/javac" -d build/transfer-tests \
		app/src/main/java/info/schnatterer/pmcaFilesystemServer/SyncStatus.java \
		app/src/main/java/info/schnatterer/pmcaFilesystemServer/TransferInputStream.java \
		tests/java/TransferInputStreamTest.java
	@"$(JAVA8_HOME)/bin/java" -cp build/transfer-tests info.schnatterer.pmcaFilesystemServer.TransferInputStreamTest
