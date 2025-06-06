# (c) 2012-2019, Ansible by Red Hat
#
# This file is part of Ansible Galaxy
#
# Ansible Galaxy is free software: you can redistribute it and/or modify
# it under the terms of the Apache License as published by
# the Apache Software Foundation, either version 2 of the License, or
# (at your option) any later version.
#
# Ansible Galaxy is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# Apache License for more details.
#
# You should have received a copy of the Apache License
# along with Galaxy.  If not, see <http://www.apache.org/licenses/>.

from copy import deepcopy
import json
import logging
import os
import shutil
from subprocess import Popen, PIPE

from galaxy_importer import constants

default_logger = logging.getLogger(__name__)


class DocStringLoader:
    """Process ansible-doc doc strings for entire collection.

    Load by calling ansible-doc once in batch for each plugin type."""

    def __init__(
        self, path, fq_collection_name, cfg, logger=None, plugin_types=None, module_path=None
    ):
        self.path = path
        self.fq_collection_name = fq_collection_name
        self.cfg = cfg
        self.log = logger or default_logger
        self.plugin_types = plugin_types
        self.module_path = module_path

    def load(self):
        self.log.info("Getting doc strings via ansible-doc")
        docs = {}

        if not shutil.which("ansible-doc"):
            self.log.warning("ansible-doc not found, skipping loading of docstrings")
            return docs

        plugin_types = self.plugin_types or constants.ANSIBLE_DOC_SUPPORTED_TYPES
        for plugin_type in plugin_types:
            # use ansible-doc to list all the plugins of this type
            found_plugins = self._run_ansible_doc_list(plugin_type)
            plugins = sorted(found_plugins.keys())

            if not plugins:
                continue

            data = self._run_ansible_doc(plugin_type, plugins)
            data = self._process_doc_strings(data)
            docs[plugin_type] = data

        return docs

    @property
    def _collections_path(self):
        return "/".join(self.path.split("/")[:-3])

    @property
    def _base_ansible_doc_cmd(self):
        return [
            "/usr/bin/env",
            f"ANSIBLE_COLLECTIONS_PATHS={self._collections_path}",
            f"ANSIBLE_COLLECTIONS_PATH={self._collections_path}",
            f"ANSIBLE_LOCAL_TEMP={self.cfg.ansible_local_tmp}",
            "ansible-doc",
        ]

    def _get_plugins(self, plugin_dir):
        """Get list of fully qualified plugin names inside directory.

        Ex: ['google.gcp.service_facts', 'google.gcp.storage.subdir2.gc_storage']
        """
        plugins = []
        for root, _, files in os.walk(plugin_dir):
            for filename in files:
                if not filename.endswith(".py") or filename == "__init__.py":
                    continue
                file_path = os.path.join(root, filename)
                sub_dirs = os.path.relpath(root, plugin_dir)

                fq_name_parts = [self.fq_collection_name]
                if sub_dirs and sub_dirs != ".":
                    fq_name_parts.extend(sub_dirs.split("/"))
                fq_name_parts.append(os.path.basename(file_path)[:-3])

                plugins.append(".".join(fq_name_parts))

        return plugins

    def _run_ansible_doc_list(self, plugin_type):
        """Use ansible-doc to get a list of plugins for the collection by type."""
        if self.module_path:
            opts = ["-M", self.module_path]
        else:
            opts = ["--list", "--type", plugin_type]
        cmd = [
            *self._base_ansible_doc_cmd,
            *opts,
            "--json",
            self.fq_collection_name,
        ]
        self.log.debug("CMD: {}".format(" ".join(cmd)))
        proc = Popen(cmd, cwd=self._collections_path, stdout=PIPE, stderr=PIPE)
        stdout, stderr = proc.communicate()
        if proc.returncode != 0:
            self.log.error(
                'Error running ansible-doc: cmd="{cmd}" returncode="{rc}" {err}'.format(
                    cmd=" ".join(cmd), rc=proc.returncode, err=stderr
                )
            )
            return {}
        return json.loads(stdout)

    def _run_ansible_doc(self, plugin_type, plugins):
        if self.module_path:
            # Use of -M allows us to get docs from any plugin type by treating
            # it as a module, by telling ansible-doc to look at a specific
            # directory
            opts = ["-M", self.module_path]
        else:
            opts = ["--type", plugin_type]
        cmd = [
            *self._base_ansible_doc_cmd,
            *opts,
            "--json",
            *plugins,
        ]
        self.log.debug("CMD: {}".format(" ".join(cmd)))
        proc = Popen(cmd, cwd=self._collections_path, stdout=PIPE, stderr=PIPE)
        stdout, stderr = proc.communicate()
        if proc.returncode != 0:
            self.log.error(
                'Error running ansible-doc: cmd="{cmd}" returncode="{rc}" {err}'.format(
                    cmd=" ".join(cmd), rc=proc.returncode, err=stderr
                )
            )
            return {}
        return json.loads(stdout)

    def _process_doc_strings(self, doc_strings):
        processed_doc_strings = {}
        for plugin_key, value in doc_strings.items():
            processed_doc_strings[plugin_key] = self._transform_doc_strings(value, self.log)
        return processed_doc_strings

    @staticmethod
    def _transform_doc_strings(data, logger=default_logger):
        """Transform data meant for UI tables into format suitable for UI."""

        def dict_to_named_list(dict_of_dict):
            """Return new list of dicts for given dict of dicts."""
            try:
                return [{"name": key, **deepcopy(dict_of_dict[key])} for key in dict_of_dict]
            except TypeError:
                logger.warning(f"Expected this to be a dictionary of dictionaries: {dict_of_dict}")
                return [
                    {"name": key, **deepcopy(dict_of_dict[key])}
                    for key in dict_of_dict
                    if isinstance(key, dict)
                ]

        def handle_nested_tables(obj, table_key):
            """Recurse over dict to replace nested tables with updated format."""
            if table_key in obj and isinstance(obj[table_key], dict):
                obj[table_key] = dict_to_named_list(obj[table_key])
                for row in obj[table_key]:
                    handle_nested_tables(row, table_key)

        doc = data.get("doc", {})
        if isinstance(doc.get("options"), dict):
            doc["options"] = dict_to_named_list(doc["options"])
            for d in doc["options"]:
                handle_nested_tables(d, table_key="suboptions")

        ret = data.get("return", None)
        if ret and isinstance(ret, dict):
            data["return"] = dict_to_named_list(ret)
            for d in data["return"]:
                handle_nested_tables(d, table_key="contains")

        return data
