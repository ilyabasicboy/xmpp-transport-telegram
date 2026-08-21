include .env

MODULE_NAME = mod_transport_telegram
BUILD_DIR = build/rel
ARCHIVE = build/module_$(MODULE_NAME)_$(MODULE_VERSION).tar.gz

.PHONY: all clean package

all: package

$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)/server/$(MODULE_NAME)/src
	mkdir -p $(BUILD_DIR)/server/$(MODULE_NAME)/conf
	mkdir -p $(BUILD_DIR)/panel/mod_transport_telegram

package: $(BUILD_DIR)
	cp module.spec $(BUILD_DIR)/module.spec
	cp server/$(MODULE_NAME)/src/$(MODULE_NAME).erl $(BUILD_DIR)/server/$(MODULE_NAME)/src/$(MODULE_NAME).erl
	cp server/$(MODULE_NAME)/conf/$(MODULE_NAME).yml $(BUILD_DIR)/server/$(MODULE_NAME)/conf/$(MODULE_NAME).yml
	cp -R mod_transport_telegram/* $(BUILD_DIR)/panel/mod_transport_telegram/
	tar -C $(BUILD_DIR) -czf $(ARCHIVE) .

clean:
	rm -rf build
