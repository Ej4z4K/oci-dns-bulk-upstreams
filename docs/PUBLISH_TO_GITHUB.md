# Publish the reusable repository

## Review the release content

Use the root README as the landing page. It contains prerequisites, shared configuration and phase commands. Link the code explanation, Mac walkthrough and generic verification guide. Review the blog, license, dependency pins, example placeholders and test status before publication.

Keep API/SSH private keys, TSIG secrets, real inventories, configuration and logs under protected ignored storage. Inspect staged content; `.gitignore` does not remove already tracked material.

## Initialize and inspect Git

Run from the package root:

```bash
git init
git add README.md LICENSE requirements.txt .gitignore scripts tests examples docs blog presentations .github
git status --short
git diff --cached --stat
git diff --cached
```

Check for private-key headers, real OCIDs/fingerprints, server identifiers and secret values. Documentation-only addresses and placeholder OCIDs are intentional. If a secret was committed, follow the relevant history cleanup and credential-rotation process.

## Publish when ready

Create the intended empty GitHub repository, then use its actual URL:

```bash
git commit -m "Add reusable TSIG-aware OCI secondary DNS upstream migration"
git branch -M main
git remote add origin YOUR_ACTUAL_GITHUB_REPOSITORY_URL
git push -u origin main
```

No placeholder represents an existing repository. Add the real clone/download URL to README after publication. The repository owner performs this publishing action.

## Reproduce the workflow

Run the offline suite in a clean environment and use the included CI configuration. Rehearse the documented phase workflow with a controlled selected zone before claiming platform or IPv6 end-to-end execution. Keep authentication out of offline CI. Add organization-specific ownership and support instructions when adopting the package.
