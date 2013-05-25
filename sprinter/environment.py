"""
A module that completely encapsulates a sprinter environment. This should be a
complete object representing any data needed by formulas.
"""

import logging
import os
import re
import shutil
import sys

from sprinter.brew import install_brew
from sprinter.manifest import Config, Manifest
from sprinter.directory import Directory
from sprinter.injections import Injections
from sprinter.system import System
from sprinter.lib import get_formula_class

from sprinter.virtualenv import create_environment as create_virtualenv

config_substitute_match = re.compile("%\(config:([^\)]+)\)")


class Environment(object):

    formula_dict = {}
    config = None  # handles the configuration, and manifests
    system = None  # stores system information
    injections = None  # handles injections
    directory = None  # handles interactions with the environment directory
    context_dict = {}

    def __init__(self, logger=None, logging_level=logging.INFO):
        self.system = System()
        self.logger = self.__build_logger(logger=logger, level=logging_level)
        if logging_level == logging.DEBUG:
            self.logger.debug("Starting in debug mode...")

    def install(self, raw_target_manifest, namespace=None, username=None, password=None):
        """
        Install an environment based on the target manifest passed
        """
        if username or password:
            assert (username and password), "both username and password required!!"
        target_manifest = Manifest(raw_target_manifest,
                                   namespace=namespace,
                                   username=username,
                                   password=password)
        directory = Directory(target_manifest.namespace)
        if not directory.new:
            self.logger.info("Namespace %s already exists, updating..." %
                             target_manifest.namespace)
            self._update(Manifest(directory.manifest_path),
                         target_manifest,
                         directory=directory)
        else:
            self.logger.info("Installing environment %s..." % target_manifest.namespace)
            self._install(target_manifest)

    def update(self, namespace, username=None, password=None):
        """
        Update a namespace
        """
        if username or password:
            assert (username and password), "both username and password required!!"
        directory = Directory(namespace)
        if directory.new:
            self.logger.error("Namespace %s is not yet installed!" % namespace)
            return
        source_manifest = Manifest(open(directory.manifest_path, 'r'))
        source = source_manifest.source()
        if not source:
            self.logger.error("Installed manifest for %s has no source!" % namespace)
            return
        target_manifest = Manifest(source, namespace=namespace, username=username, password=password)
        self.logger.info("Updating environment %s..." % target_manifest.namespace)
        self._update(source_manifest, target_manifest)

    def remove(self, namespace):
        """
        Remove an environment namespace
        """
        directory = Directory(namespace)
        if directory.new:
            self.logger.error("Namespace %s does not exist!" % namespace)
            return
        source_manifest = Manifest(directory.manifest_path)
        self.logger.info("Removing environment %s..." % source_manifest.namespace)
        self._remove(source_manifest, directory=directory)

    def deactivate(self, namespace):
        """
        Deactivate an environment namespace
        """
        directory = Directory(namespace, rewrite_rc=False)
        if directory.new:
            self.logger.error("Namespace %s does not exist!" % namespace)
            return
        source_manifest = Manifest(directory.manifest_path)
        self.logger.info("Deactivating environment %s..." % source_manifest.namespace)
        self._deactivate(source_manifest, directory=directory)

    def activate(self, namespace):
        """
        Activate an environment namespace
        """
        directory = Directory(namespace, rewrite_rc=False)
        if directory.new:
            self.logger.error("Namespace %s does not exist!" % namespace)
            return
        source_manifest = Manifest(directory.manifest_path)
        self.logger.info("Activating environment %s..." % source_manifest.namespace)
        self._activate(source_manifest, directory=directory)

    def reload(self, namespace):
        """
        Activate an environment namespace
        """
        directory = Directory(namespace, rewrite_rc=False)
        if directory.new:
            self.logger.error("Namespace %s does not exist!" % namespace)
            return
        source_manifest = Manifest(directory.manifest_path)
        self.logger.info("Reloading environment %s..." % source_manifest.namespace)
        self._reload(source_manifest, directory=directory)

    def initialize(self, source_manifest=None, target_manifest=None, directory=None, new=False):
        """
        Initialize the environment for a sprinter action
        """
        self.config = Config(source=source_manifest, target=target_manifest)
        self.directory = directory if directory else Directory(self.config.namespace)
        self.directory.initialize()
        if new or (target_manifest.is_true('config', 'virtualenv') and \
               not source_manifest.is_true('config', 'virtualenv')):
            self.logger.info("Installing Virtualenv...")
            create_virtualenv(self.directory.root_dir,
                              use_distribute=True)
        self.injections = Injections(wrapper="SPRINTER_%s" % self.config.namespace)
        self.config.grab_inputs(target_manifest if target_manifest else source_manifest)
        self.logger.info("Installing Brew...")
        os.environ['PATH'] = self.directory.bin_path + ":" + os.environ['PATH']
        install_brew(self.directory.root_dir)
        kind = 'target' if target_manifest else 'source'
        self.context_dict = self.__generate_context_dict(kind=kind)

    def finalize(self):
        """ command to run at the end of sprinter's run """
        self.logger.debug("Finalizing...")
        if os.path.exists(self.directory.manifest_path):
            self.config.write(open(self.directory.manifest_path, "w+"))
        if self.directory.rewrite_rc:
            self.directory.add_to_rc("export PATH=%s:$PATH" % self.directory.bin_path())
        self.injections.commit()

    def context(self):
        """ get a context dictionary to replace content """
        return self.context_dict

    def validate_context(self, content):
        """ check if all the config variables desired exist, and prompt them if not """
        values = config_substitute_match.findall(content)
        for v in values:
            if v not in self.manifest.config:
                self.get_config(v, default=None, temporary=False)

    def validate_manifest(self, manifest_path, username=None, password=None):
        """ run a validation on a manifest, and return any errors"""
        m = Manifest(manifest_path, username=username, password=password)
        return m.invalidations

    def _install(self, target_manifest, directory=None):
        """
        Intall an environment from a target manifest Manifest
        """
        self.initialize(target_manifest=target_manifest, 
                        directory=directory,
                        new=True)
        self._run_setups()
        self.injections.inject("~/.bash_profile", "[ -d %s ] && . %s/.rc" %
                               (self.directory.root_dir, self.directory.root_dir))
        self.injections.inject("~/.bashrc", "[ -d %s ] && . %s/.rc" %
                               (self.directory.root_dir, self.directory.root_dir))
        self.finalize()

    def _update(self, source_manifest, target_manifest, directory=None):
        """
        Intall an environment from a target manifest Manifest
        """
        self.initialize(source_manifest=source_manifest,
                        target_manifest=target_manifest,
                        directory=directory)
        self._run_setups()
        self._run_updates()
        self._run_destroys()
        self.finalize()

    def _remove(self, source_manifest, directory=None):
        """
        Remove an environment defined by a source_manifest
        """
        self.initialize(source_manifest=source_manifest)
        self._run_destroys()
        self.injections.clear("~/.bash_profile")
        shutil.rmtree(self.directory.root_dir)
        self.finalize()

    def _deactivate(self, source_manifest, directory=None):
        """
        Remove an environment defined by a source_manifest
        """
        self.initialize(source_manifest=source_manifest, directory=directory)
        self._run_deactivates()
        self.injections.clear("~/.bash_profile")
        self.finalize()

    def _activate(self, source_manifest, directory=None):
        """
        Remove an environment defined by a source_manifest
        """
        self.initialize(source_manifest=source_manifest, directory=directory)
        self._run_activates()
        self.injections.inject("~/.bash_profile", "[ -d %s ] && . %s/.rc" %
                               (self.directory.root_dir, self.directory.root_dir))
        self.injections.inject("~/.bashrc", "[ -d %s ] && . %s/.rc" %
                               (self.directory.root_dir, self.directory.root_dir))
        self.finalize()

    def _reload(self, source_manifest, directory=None):
        """
        Reload an environment defined by a source_manifest
        """
        self.initialize(source_manifest=source_manifest, directory=directory)
        self._run_reloads()
        self.finalize()

    def _run_setups(self):
        for name, config in self.config.setups():
            self.logger.info("Setting up %s..." % name)
            formula_instance = self.__get_formula_instance(config['target']['formula'])
            specialized_config = self.__substitute_objects(config['target'])
            if self._phases(specialized_config, 'setup'):
                formula_instance.setup(name, specialized_config)

    def _run_updates(self):
        for name, config in self.config.updates():
            self.logger.info("Updating %s..." % name)
            formula_instance = self.__get_formula_instance(config['target']['formula'])
            specialized_config = self.__substitute_objects(config)
            if self._phases(specialized_config['target'], 'update'):
                formula_instance.update(name,
                                        specialized_config['source'],
                                        specialized_config['target'])

    def _run_destroys(self):
        for name, config in self.config.destroys():
            self.logger.info("Removing %s..." % name)
            formula_instance = self.__get_formula_instance(config['source']['formula'])
            if self._phases(config['source'], 'destroy'):
                formula_instance.destroy(name, config['source'])

    def _run_activates(self):
        for name, config in self.config.activations():
            self.logger.info("Activating %s..." % name)
            formula_instance = self.__get_formula_instance(config['source']['formula'])
            if self._phases(config['source'], 'activate'):
                formula_instance.activate(name, config['source'])

    def _run_deactivates(self):
        for name, config in self.config.deactivations():
            self.logger.info("Deactivating %s..." % name)
            formula_instance = self.__get_formula_instance(config['source']['formula'])
            if self._phases(config['source'], 'deactivate'):
                formula_instance.deactivate(name, config['source'])

    def _run_reloads(self):
        for name, config in self.config.reloads():
            self.logger.info("Reloading %s..." % name)
            formula_instance = self.__get_formula_instance(config['source']['formula'])
            specialized_config = self.__substitute_objects(config['source'])
            if self._phases(specialized_config['source'], 'reload'):
                formula_instance.reload(name, specialized_config)

    def __build_logger(self, logger=None, level=logging.INFO):
        """ return a logger. if logger is none, generate a logger from stdout """
        if not logger:
            logger = logging.getLogger('sprinter')
            out_hdlr = logging.StreamHandler(sys.stdout)
            out_hdlr.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
            out_hdlr.setLevel(level)
            logger.addHandler(out_hdlr)
        logger.setLevel(level)
        return logger

    def __get_formula_instance(self, formula):
        """
        get an instance of the formula object object if it exists, else
        create one, add it to the dict, and pass return it.
        """
        if formula not in self.formula_dict:
            self.formula_dict[formula] = get_formula_class(formula, self)
        return self.formula_dict[formula]

    def __generate_context_dict(self, kind='target'):
        context_dict = self.config.get_context_dict(kind=kind)
        manifest = self.config.target if kind == 'target' else self.config.source
        if manifest:
            for s in manifest.formula_sections():
                context_dict["%s:root_dir" % s] = self.directory.install_directory(s)
            # add environment information
            context_dict['config:node'] = self.system.node
        return context_dict

    def __substitute_objects(self, value):
        """
        recursively substitute value with the context_dict
        """
        if type(value) == dict:
            return dict([(k, self.__substitute_objects(v)) for k, v in value.items()])
        elif type(value) == str:
            return value % self.context_dict
        else:
            return value

    def _phases(self, config, phase_name):
        """
        Return true if the phase should be run. False otherwise.
        """
        return ('phases' not in config or
                phase_name in [x.strip() for x in config['phases'].split(",")])
