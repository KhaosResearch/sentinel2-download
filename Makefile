build:
	@python -m build
	
release:
	@python -m twine upload --skip-existing -r khaos dist/ds_download*
	
.DEFAULT_GOAL :=
all: build release