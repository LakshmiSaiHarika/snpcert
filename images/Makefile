IMAGES := $(sort $(notdir $(wildcard host-* guest-*)))
GENERATE_MANIFEST ?=
CLEAN_IMAGES := $(addprefix clean-,$(IMAGES))

# github.com/<repo>/releases/latest redirects to .../releases/tag/<tag>; unlike the
# REST API this needs no token and is not subject to the 60 requests/hour limit
GH_LATEST = curl -sfI -o /dev/null -w '%{redirect_url}' https://github.com/$(1)/releases/latest | sed -n 's|.*/releases/tag/||p'

# Pinned component versions (override on the command line with make VAR=x)
VERSIONS_ENV := ../versions.env
include $(VERSIONS_ENV)

# $(call tag,VAR,owner/repo): the value of VAR from ../versions.env or, if empty,
# the latest upstream release. The latest tag is fetched at most once per make
# run, and only when an image build needs it.
latest = $(or $(shell $(call GH_LATEST,$(1))),$(error Failed to fetch latest release tag for $(1); pin it in $(VERSIONS_ENV) or check network access to github.com))
tag = $(or $($(1)),$(if $(filter undefined,$(origin $(1)_LATEST)),$(eval $(1)_LATEST := $(call latest,$(2))))$($(1)_LATEST))

TAGS = SNPGUEST_TAG=$(call tag,SNPGUEST_TAG,virtee/snpguest) \
       BEACON_TAG=$(call tag,BEACON_TAG,AMDEPYC/beacon) \
       SNPHOST_TAG=$(call tag,SNPHOST_TAG,virtee/snphost)

.PHONY: all clean list status versions $(IMAGES) $(CLEAN_IMAGES)

# Validate ../versions.env and show the tags image builds will use
versions:
	@bad=$$(grep -nvE '^[[:space:]]*(#|$$)|^(SNPHOST_TAG|SNPGUEST_TAG|BEACON_TAG)=[^[:space:]]*$$' $(VERSIONS_ENV) || true); \
	if [ -n "$$bad" ]; then \
		echo "$(VERSIONS_ENV): unexpected line(s); expected NAME=TAG with NAME one of SNPHOST_TAG, SNPGUEST_TAG, BEACON_TAG" >&2; \
		echo "$$bad" >&2; \
		exit 1; \
	fi
	@printf '%s\n' $(TAGS)

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
$(IMAGES): | versions
	@MKOSI_ENV="$(addprefix --environment=,$(TAGS))"; \
	case "$(GENERATE_MANIFEST)" in \
		y|yes|1) MKOSI_ENV="$$MKOSI_ENV --environment=GENERATE_MANIFEST=yes";; \
	esac; \
	/home/amd/mkosi-v26/mkosi/bin/mkosi --image-id=$@ -C $@ build $$MKOSI_ENV

$(CLEAN_IMAGES):
	mkosi --image-id=$(patsubst clean-%,%,$@) -C $(patsubst clean-%,%,$@) clean
