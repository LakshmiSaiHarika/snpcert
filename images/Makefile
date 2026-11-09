IMAGES := $(sort $(notdir $(wildcard host-* guest-*)))
GENERATE_MANIFEST ?=
CLEAN_IMAGES := $(addprefix clean-,$(IMAGES))

GH_AUTH := $(if $(GITHUB_TOKEN),-H "Authorization: Bearer $(GITHUB_TOKEN)")
GH_RELEASE = curl -sf $(GH_AUTH) https://api.github.com/repos/$(1)/releases/$(2) | jq -r '.tag_name'

# Pinned component versions (override on the command line with make VAR=x)
VERSIONS_ENV := ../versions.env
include $(VERSIONS_ENV)

.PHONY: all clean list status tags $(IMAGES) $(CLEAN_IMAGES)

# Pinned versions from ../versions.env are checked to exist upstream and used
# as-is; empty entries fall back to fetching the latest upstream release.
tags:
	@set -e; \
	bad=$$(grep -nvE '^[[:space:]]*(#|$$)|^(SNPHOST_TAG|SNPGUEST_TAG|BEACON_TAG)=[^[:space:]]*$$' $(VERSIONS_ENV) || true); \
	if [ -n "$$bad" ]; then \
		echo "$(VERSIONS_ENV): unexpected line(s); expected NAME=TAG with NAME one of SNPHOST_TAG, SNPGUEST_TAG, BEACON_TAG" >&2; \
		echo "$$bad" >&2; \
		exit 1; \
	fi; \
	trap 'rm -f .tags.tmp' EXIT; \
	fetch() { \
		if [ -n "$$3" ]; then \
			tag=$$($(call GH_RELEASE,$$1,tags/$$3)); \
			hint="Check $$2 in $(VERSIONS_ENV) is an existing release of $$1"; \
		else \
			tag=$$($(call GH_RELEASE,$$1,latest)); \
			hint="Failed to fetch latest tag for $$1"; \
		fi; \
		if [ -z "$$tag" ] || [ "$$tag" = "null" ]; then \
			echo "$$hint. If rate-limited, try: export GITHUB_TOKEN=\$$(gh auth token)" >&2; \
			exit 1; \
		fi; \
		echo "$$2=$$tag"; \
	}; \
	{ fetch virtee/snpguest SNPGUEST_TAG "$(SNPGUEST_TAG)"; \
	  fetch AMDEPYC/beacon   BEACON_TAG    "$(BEACON_TAG)"; \
	  fetch virtee/snphost   SNPHOST_TAG   "$(SNPHOST_TAG)"; } > .tags.tmp; \
	mv .tags.tmp .tags
	@cat .tags

.tags:
	@echo "Missing .tags — run 'make tags' first to fetch component versions." >&2; exit 1

all: $(IMAGES)

clean: $(CLEAN_IMAGES)

list:
	@echo $(IMAGES)

status:
	@for img in $(IMAGES); do \
		if [ -e $$img/$$img ]; then \
			echo "$$img:"; \
			ls $$img/$$img $$img/$$img.* 2>/dev/null | sed 's/^/\t/'; \
		fi; \
	done

# MKOSI_ENV accumulates --environment flags; add new case blocks to extend
$(IMAGES): | .tags
	@MKOSI_ENV=""; \
	case "$(GENERATE_MANIFEST)" in \
		y|yes|1) MKOSI_ENV="$$MKOSI_ENV --environment=GENERATE_MANIFEST=yes";; \
	esac; \
	mkosi --image-id=$@ -C $@ --env-file=../.tags build $$MKOSI_ENV

$(CLEAN_IMAGES):
	mkosi --image-id=$(patsubst clean-%,%,$@) -C $(patsubst clean-%,%,$@) clean
