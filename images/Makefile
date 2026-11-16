IMAGES := $(sort $(notdir $(wildcard host-* guest-*)))
GENERATE_MANIFEST ?=
CLEAN_IMAGES := $(addprefix clean-,$(IMAGES))

# Pinned component versions (override on the command line with make VAR=x).
# An empty value means "use the latest upstream release".
VERSIONS_ENV := ../versions.env
include $(VERSIONS_ENV)

# $(call latest_release,owner/repo)
#   Returns the tag of the latest GitHub release of owner/repo, e.g.
#   $(call latest_release,virtee/snpguest) -> v0.11.0
#
#   How it works:
#     1. curl requests github.com/<owner/repo>/releases/latest without following
#        redirects (-I: headers only, -o /dev/null: discard the body) and
#        prints the redirect target, .../releases/tag/<tag>.
#     2. sed keeps only the part after "/releases/tag/", i.e. the tag.
#     3. If the result is empty (network error, unknown repo, no releases),
#        $(error) stops make with a hint to pin the tag in versions.env.
#
#   The redirect is used instead of the GitHub REST API because it needs no
#   token and is not subject to the API's 60 requests/hour anonymous limit.
define latest_release
$(or
    $(shell curl -sfI -o /dev/null -w '%{redirect_url}' https://github.com/$(1)/releases/latest | sed -n 's|.*/releases/tag/||p'),
    $(error Failed to fetch latest release tag for $(1); pin it in $(VERSIONS_ENV) or check network access to github.com))
endef

# $(call resolve_tag,VAR,owner/repo)
#   Returns the tag to build with for one component, e.g.
#   $(call resolve_tag,SNPGUEST_TAG,virtee/snpguest)
#
#   $(or) returns the first non-empty value of:
#     1. $(VAR)        - the version pinned in versions.env or on the command
#                        line (make VAR=x).
#     2. $(VAR_LATEST) - the latest release, if it was already fetched earlier
#                        in this make run.
#     3. otherwise     - fetch the latest release with latest_release, store
#                        it in VAR_LATEST via $(eval), and return it.
#
#   Because TAGS is a recursive (=) variable, this runs only when a target
#   actually uses the tags, and the VAR_LATEST cache ensures each repo is
#   queried at most once per make run even though TAGS is expanded by both
#   the versions target and every image build.
define resolve_tag
$(or
    $($(1)),
    $($(1)_LATEST),
    $(eval $(1)_LATEST := $(call latest_release,$(2)))$($(1)_LATEST))
endef

# $(call check_versions_env,file)
#   Recipe that fails, listing the offending lines, if file contains anything
#   other than blank lines, comments, or NAME=TAG for a known NAME.
define check_versions_env
@bad=$$(grep -nvE '^[[:space:]]*(#|$$)|^(SNPHOST_TAG|SNPGUEST_TAG|BEACON_TAG)=[^[:space:]]*$$' $(1) || true); \
if [ -n "$$bad" ]; then \
	echo "$(1): unexpected line(s); expected NAME=TAG with NAME one of SNPHOST_TAG, SNPGUEST_TAG, BEACON_TAG" >&2; \
	echo "$$bad" >&2; \
	exit 1; \
fi
endef

# Component tags passed to every image build, as NAME=TAG
TAGS = SNPGUEST_TAG=$(call resolve_tag,SNPGUEST_TAG,virtee/snpguest) \
       BEACON_TAG=$(call resolve_tag,BEACON_TAG,AMDEPYC/beacon) \
       SNPHOST_TAG=$(call resolve_tag,SNPHOST_TAG,virtee/snphost)

.PHONY: all clean list status versions $(IMAGES) $(CLEAN_IMAGES)

# Validate ../versions.env and show the tags image builds will use
versions:
	$(call check_versions_env,$(VERSIONS_ENV))
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
	/home/amd/mkosi-v26/mkosi/bin/mkosi --image-id=$(patsubst clean-%,%,$@) -C $(patsubst clean-%,%,$@) clean
