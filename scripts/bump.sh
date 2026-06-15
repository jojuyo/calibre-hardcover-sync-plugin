#!/usr/bin/env bash
plugin_name="${1:-hardcover-sync}"

cliff() {
	git cliff --include-path "**/*" --tag-pattern "$plugin_name"'-*' $@
}

new_tag=$(cliff --bumped-version)
new_version="${new_tag/"$plugin_name-"/}"
echo $new_version
if [ $(git tag -l "$new_tag") ]; then
	echo "NOTE: tag $new_tag already exists"
fi

echo "$plugin_name version will be bumped to $new_version. Continue?"
select yn in "Yes" "No"; do
    case $yn in
        Yes ) git tag -f "$new_tag"; break;;
        No ) exit;;
    esac
done
echo "Run 'git push --tags' to Release"
