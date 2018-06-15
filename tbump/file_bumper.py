from typing import Dict, List, Optional, Pattern
import attr
import re

from path import Path
import ui

import tbump
import tbump.git
import tbump.config


# pylint: disable=pointless-statement
List, Optional, Pattern


# pylint: disable=too-few-public-methods
@attr.s
class ChangeRequest:
    src = attr.ib()   # type: str
    old_string = attr.ib()  # type: str
    new_string = attr.ib()  # type: str
    search = attr.ib(default=None)  # type: Optional[str]


# pylint: disable=too-few-public-methods
@attr.s
class Patch:
    src = attr.ib()  # type: str
    lineno = attr.ib()  # type: int
    old_line = attr.ib()  # type: str
    new_line = attr.ib()  # type: str


def print_patch(patch: Patch) -> None:
    ui.info(
        ui.red, "- ", ui.reset,
        ui.bold, patch.src, ":", ui.reset,
        ui.darkgray, patch.lineno + 1, ui.reset,
        " ", ui.red, patch.old_line.strip(),
        sep=""
    )
    ui.info(
        ui.green, "+ ", ui.reset,
        ui.bold, patch.src, ":", ui.reset,
        ui.darkgray, patch.lineno + 1, ui.reset,
        " ", ui.green, patch.new_line.strip(),
        sep=""
    )


class BadSubstitution(tbump.Error):
    def __init__(self, *, src: str, verb: str,
                 groups: Dict[str, str], template: str, version: str) -> None:
        super().__init__()
        self.src = src
        self.verb = verb
        self.groups = groups
        self.template = template
        self.version = version

    def print_error(self) -> None:
        message = [
            " ", self.src + ":",
            " refusing to ", self.verb, " version containing 'None'\n",
        ]
        message += [
            "More info:\n",
            " * version groups:  ", repr(self.groups), "\n"
            " * template:        ", self.template, "\n",
            " * version:         ", self.version, "\n",
        ]
        ui.error(*message, end="", sep="")


class InvalidVersion(tbump.Error):
    def __init__(self, *, version: str, regex: Pattern) -> None:
        super().__init__()
        self.version = version
        self.regex = regex

    def print_error(self) -> None:
        ui.error("Could not parse", self.version, "as a valid version string")


class SourceFileNotFound(tbump.Error):
    def __init__(self, *, src: str) -> None:
        super().__init__()
        self.src = src

    def print_error(self) -> None:
        ui.error(self.src, "does not exist")


class CurrentVersionNotFound(tbump.Error):
    def __init__(self, *, src: str, current_version_string: str) -> None:
        super().__init__()
        self.src = src
        self.current_version_string = current_version_string

    # TODO: raise just once for all errors
    def print_error(self) -> None:
        ui.error(
            "Current version string: (%s)" % self.current_version_string,
            "not found in", self.src
        )


def should_replace(line: str, old_string: str, search: Optional[str] = None) -> bool:
    if not search:
        return old_string in line
    else:
        return (old_string in line) and (search in line)


def on_version_containing_none(src: str, verb: str, version: str, *,
                               groups: Dict[str, str], template: str) -> None:
    raise BadSubstitution(src=src, verb=verb, version=version, groups=groups, template=template)


class FileBumper():
    def __init__(self, working_path: Path) -> None:
        self.working_path = working_path
        self.files = list()  # type: List[tbump.config.File]
        self.version_regex = re.compile(".")
        self.current_version = ""
        self.current_groups = dict()  # type: Dict[str, str]
        self.new_version = ""
        self.new_groups = dict()  # type: Dict[str, str]

    def parse_version(self, version: str) -> Dict[str, str]:
        assert self.version_regex
        match = self.version_regex.fullmatch(version)
        if match is None:
            raise InvalidVersion(version=version, regex=self.version_regex)
        return match.groupdict()

    def set_config(self, config: tbump.config.Config) -> None:
        self.files = config.files
        self.check_files_exist()
        self.version_regex = config.version_regex
        self.current_version = config.current_version
        self.current_groups = self.parse_version(self.current_version)

    def check_files_exist(self) -> None:
        assert self.files
        for file in self.files:
            expected_path = self.working_path.joinpath(file.src)
            if not expected_path.exists():
                raise SourceFileNotFound(src=file.src)

    def compute_patches(self, new_version: str) -> List[Patch]:
        self.new_version = new_version
        self.new_groups = self.parse_version(self.new_version)
        change_requests = self.compute_change_requests()
        tbump_toml_change = ChangeRequest("tbump.toml", self.current_version, new_version)
        change_requests.append(tbump_toml_change)
        patches = list()
        for change_request in change_requests:
            patches_for_request = self.compute_patches_for_change_request(change_request)
            patches.extend(patches_for_request)
        return patches

    # pylint: disable=invalid-name
    def compute_patches_for_change_request(self, change_request: ChangeRequest) -> List[Patch]:
        old_string = change_request.old_string
        new_string = change_request.new_string
        search = change_request.search

        file_path = Path(self.working_path).joinpath(change_request.src)
        old_lines = file_path.lines()

        patches = list()
        for i, old_line in enumerate(old_lines):
            if should_replace(old_line, old_string, search):
                new_line = old_line.replace(old_string, new_string)
                patch = Patch(change_request.src, i, old_line, new_line)
                patches.append(patch)
        if not patches:
            raise CurrentVersionNotFound(src=change_request.src, current_version_string=old_string)
        return patches

    def compute_change_requests(self) -> List[ChangeRequest]:
        change_requests = list()
        for file in self.files:
            change_request = self.compute_change_request_for_file(file)
            change_requests.append(change_request)
        return change_requests

    def compute_change_request_for_file(self, file: tbump.config.File) -> ChangeRequest:
        if file.version_template:
            current_version = file.version_template.format(**self.current_groups)
            if "None" in current_version:
                on_version_containing_none(
                    file.src,
                    "look for",
                    current_version,
                    groups=self.current_groups,
                    template=file.version_template
                )
            new_version = file.version_template.format(**self.new_groups)
            if "None" in new_version:
                on_version_containing_none(
                    file.src,
                    "replace by",
                    new_version,
                    groups=self.new_groups,
                    template=file.version_template
                )
        else:
            current_version = self.current_version
            new_version = self.new_version

        to_search = None
        if file.search:
            to_search = file.search.format(current_version=current_version)

        return ChangeRequest(file.src, current_version, new_version, search=to_search)

    def apply_patches(self, patches: List[Patch]) -> None:
        for patch in patches:
            print_patch(patch)
            file_path = Path(self.working_path).joinpath(patch.src)
            # TODO: read and write each file only once?
            lines = file_path.lines()
            lines[patch.lineno] = patch.new_line
            file_path.write_lines(lines)
