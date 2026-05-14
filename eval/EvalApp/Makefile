DEVELOPER_DIR := /Applications/Xcode.app/Contents/Developer
DESTINATION := generic/platform=iOS Simulator
DERIVED_DATA := $(CURDIR)/build/DerivedData
APP_BUNDLE := $(CURDIR)/build/EvalApp.app

.PHONY: build clean

build:
	@mkdir -p build
	DEVELOPER_DIR=$(DEVELOPER_DIR) xcodebuild \
		-project EvalApp.xcodeproj \
		-scheme EvalApp \
		-configuration Debug \
		-sdk iphonesimulator \
		-arch arm64 \
		-derivedDataPath $(DERIVED_DATA) \
		build
	rm -rf $(APP_BUNDLE)
	cp -R $(DERIVED_DATA)/Build/Products/Debug-iphonesimulator/EvalApp.app $(APP_BUNDLE)
	@echo "Built: $(APP_BUNDLE)"

clean:
	rm -rf build
