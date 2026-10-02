.PHONY: update-headers

update-headers:
	@head -2 LICENSE > /tmp/spdx.txt
	@echo "" >> /tmp/spdx.txt
	licenseheaders --tmpl /tmp/spdx.txt --dir . \
		-x "*/.*" ".venv/*" ".git/*" ".gitlab/*" ".github/*" ".vscode/*" \
		   "doc/build/*" "ext/*" "src/sionna/rt/*" "tutorials/rt/*" \
		   "doc/source/rt/*" "doc/source/rk/*" \
		   "doc/source/phy/tutorials/notebooks/*" "doc/source/sys/tutorials/notebooks/*"
	@rm /tmp/spdx.txt
	@echo "Removing extra blank comment lines..."
	@find . -name "*.py" -type f ! -path "./.venv/*" ! -path "./ext/*" \
		-exec sed -i ':a;N;$$!ba;s/Apache-2.0\n#\n#/Apache-2.0\n#/g' {} \;
