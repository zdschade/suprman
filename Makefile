# suprman -- install with `make install`, or `./install.sh --user`.
#
# The Python package is small enough to install as plain files: no build step,
# no venv, no third party Python packages.  PyGObject/GTK4 come from the distro
# (see README.md for the per-distro package names).

PREFIX  ?= /usr/local
BINDIR  ?= $(PREFIX)/bin
LIBDIR  ?= $(PREFIX)/lib/suprman
DATADIR ?= $(PREFIX)/share
DESTDIR ?=
PYTHON  ?= python3

VERSION := $(shell sed -n 's/^__version__ = "\(.*\)"/\1/p' src/suprman/__init__.py)

.PHONY: all run test lint check install uninstall example-config clean dist

all: check

run:
	PYTHONPATH=src $(PYTHON) -m suprman

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -t . -v

lint:
	@if command -v ruff >/dev/null 2>&1; then ruff check src tests; \
	else echo "ruff not installed, skipping"; fi

check:
	$(PYTHON) -m compileall -q src tests
	@echo "syntax ok"

example-config:
	PYTHONPATH=src $(PYTHON) -m suprman --example-config

install:
	install -d $(DESTDIR)$(LIBDIR)/suprman/data
	install -m 0644 src/suprman/*.py $(DESTDIR)$(LIBDIR)/suprman/
	install -m 0644 src/suprman/data/* $(DESTDIR)$(LIBDIR)/suprman/data/
	install -d $(DESTDIR)$(BINDIR)
	install -m 0755 bin/suprman $(DESTDIR)$(BINDIR)/suprman
	install -d $(DESTDIR)$(DATADIR)/applications
	install -m 0644 data/suprman.desktop $(DESTDIR)$(DATADIR)/applications/suprman.desktop
	# Precompile so even the first launch skips source parsing. The .pyc files
	# stay valid when a packager moves DESTDIR into place.
	$(PYTHON) -m compileall -q $(DESTDIR)$(LIBDIR) >/dev/null 2>&1 || true
	@echo "installed suprman $(VERSION) into $(DESTDIR)$(PREFIX)"

uninstall:
	rm -rf $(DESTDIR)$(LIBDIR)
	rm -f $(DESTDIR)$(BINDIR)/suprman
	rm -f $(DESTDIR)$(DATADIR)/applications/suprman.desktop

# Source tarball for distro packagers; a changelog and licence are all a spec
# or PKGBUILD needs on top of this.
dist:
	@mkdir -p dist
	@git archive --prefix=suprman-$(VERSION)/ -o dist/suprman-$(VERSION).tar.gz HEAD 2>/dev/null \
		|| tar --exclude=./dist --exclude=.git --exclude=__pycache__ \
			-czf dist/suprman-$(VERSION).tar.gz --transform 's,^\.,suprman-$(VERSION),' .
	@echo "dist/suprman-$(VERSION).tar.gz"

clean:
	rm -rf dist
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
