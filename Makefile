-include .env

JAVA8_HOME ?= $(JAVA_HOME)
ANDROID_SDK_ROOT ?= $(ANDROID_HOME)
GRADLEW := bash ./gradlew
GRADLE_ENV := JAVA_HOME="$(JAVA8_HOME)" ANDROID_SDK_ROOT="$(ANDROID_SDK_ROOT)" ANDROID_HOME="$(ANDROID_SDK_ROOT)"

.PHONY: help doctor build build-no-tests test test-unit apk apk-debug apk-release apk-release-signed clean

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
		'  make clean           - Clean build outputs' \
		'  make doctor          - Print configured paths'

doctor:
	@printf 'JAVA8_HOME=%s\nANDROID_SDK_ROOT=%s\nANDROID_HOME=%s\n' \
		"$(JAVA8_HOME)" "$(ANDROID_SDK_ROOT)" "$(ANDROID_SDK_ROOT)"

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

clean:
	@$(GRADLE_ENV) $(GRADLEW) clean
