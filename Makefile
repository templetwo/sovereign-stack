.PHONY: check hooks

check:
	scripts/check.sh

hooks:
	git config core.hooksPath .githooks
	@echo "core.hooksPath set to .githooks"
