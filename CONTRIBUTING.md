# Contributing to ComfyStream

Hey there, potential contributor!

Thanks for your interest in this project. Every contribution is welcome and
appreciated. We're super excited to help you to get started 😎

> **Note:** If you still have questions after reading through this guide,
> [open an issue](https://github.com/livepeer/comfystream/issues) or
> [talk to us on Discord](https://discordapp.com/invite/7wRSUGX).

### How You Can Help

ComfyStream contributions will generally fall into one of the following
categories:

#### 📖 Updating documentation

This could be as simple as adding some extra notes to a README.md file, or as
complex as creating some new `package.json` scripts to generate docs. Either
way, we'd really really love your help with this 💖. Look for
[open documentation issues](https://github.com/livepeer/comfystream/labels/type%3A%20documentation),
create your own, or submit a PR with the updates you want to see.

If you're interested in updating documentation, please refer to the relevant documentation on [pipelines.livepeer.org/docs](https://pipelines.livepeer.org/docs/technical/getting-started/install-comfystream).

#### 💬 Getting involved in issues
As a starting point, check out the issues that we've labeled as 
[help wanted](https://github.com/livepeer/comfystream/issues?q=is:issue%20is:open)
and
[good first issues](https://github.com/livepeer/comfystream/labels/good%20first%20issue).

Many issues are open discussions. Feel free to add your own concerns, ideas, and
workarounds. If you don't see what you're looking for, you can always open a new
issue. 

#### 🐛 Fixing bugs, 🕶️ adding feature/enhancements, or 👌 improving code quality

If you're into coding, maybe try fixing a
[bug](https://github.com/livepeer/comfystream/issues?q=is%3Aissue%20is%3Aopen%20label%3Abug)
or taking on a
[feature request](https://github.com/livepeer/comfystream/issues?q=is%3Aissue%20is%3Aopen%20label%3Aenhancement).

If picking up issues isn't your thing, no worries -- you can always add more
tests to improve coverage or refactor code to increase maintainability. 

> Note: Bonus points if you can delete code instead of adding it! 👾

#### 🛠️ Updating scripts and tooling

We want to make sure ComfyStream contributors have a pleasant developer
experience (DX). The tools we use to code continually change and improve. If you
see ways to reduce the amount of repetition or stress when it comes to coding in
this project, feel free to create an issue and/or PR to discuss. Let's continue
to improve this codebase for everyone.

> Note: These changes generally affect multiple packages, so you'll probably
> want to be familiar with each project's layout and conventions. Because of
> this additional cognitive load, you may not want to begin here for you first
> contribution.

#### Commits

- We generally prefer to logically group changes into individual commits as much as possible and like to follow these [guidelines](https://github.com/lightningnetwork/lnd/blob/master/docs/code_contribution_guidelines.md#ideal-git-commit-structure) for commit structure
- We like to use [fixup commits and auto-squashing when rebasing](https://thoughtbot.com/blog/autosquashing-git-commits) during the PR review process to a) make it easy to review incremental changes and b) make it easy to logically group changes into individual commits
- We like to use descriptive commit messages following these [guidelines](https://github.com/lightningnetwork/lnd/blob/master/docs/code_contribution_guidelines.md#model-git-commit-messages). Additionally, we like to prefix commit titles with the package/functionality that the commit updates (i.e. `api: ...`) as described [here](https://github.com/lightningnetwork/lnd/blob/master/docs/code_contribution_guidelines.md#ideal-git-commit-structure)

## FAQ

### How is a contribution reviewed and accepted?

- **If you are opening an issue...**

  - Fill out all required sections for your issue type. Issues that are not
    filled out properly will be flagged as `need: more info` and will be closed if not
    updated.
  - _Keep your issue simple, clear, and to-the-point_. Most issues do not
    require many paragraphs of text. In fact, if you write too much, it's
    difficult to understand what you are actually trying to communicate.
    **Consider

- **If you are submitting a pull request...**
  - Write tests to increase code coverage
  - Tag the issue(s) your PR is closing or relating to
  - Make sure your PR is up-to-date with `master` (rebase please 🙏)
  - Wait for a maintainer to review your PR
  - Push additional commits to your PR branch to fix any issues noted in review.
    - Avoid force pushing to the branch which will clobber commit history and makes it more difficult for reviewing incremental changes in the PR
    - Instead use [fixup commits](#commits) that can be squashed prior to merging
  - Wait for a maintainer to merge your PR
    - For a small changesets, the Github "squash and merge" option can be an acceptable
    - For larger changesets, the Github "rebase and merge" option is preferable and a maintainer may request you do a local rebase first to cleanup the branch commit history before merging

### When is it appropriate to follow up?

You can expect a response from a maintainer within 7 days. If you haven’t heard
anything by then, feel free to ping the thread.

### How much time is spent on this project?

Currently, there are several teams dedicated to maintaining this project.

### What types of contributions are accepted?

All of the types outlined in [How You Can Help](#how-you-can-help).

### What happens if my suggestion or PR is not accepted?

While it's unlikely, sometimes there's no acceptable way to implement a
suggestion or merge a PR. If that happens, maintainer will still...

- Thank you for your contribution.
- Explain why it doesn’t fit into the scope of the project and offer clear
  suggestions for improvement, if possible.
- Link to relevant documentation, if it exists.
- Close the issue/request.

But do not despair! In many cases, this can still be a great opportunity to
follow-up with an improved suggestion or pull request. Worst case, this repo is
open source, so forking is always an option 😎.
