import logging
import os
import sys
from StringIO import StringIO
from functools import wraps

from sprinter.core import PHASES
from sprinter import brew
from sprinter import lib
from sprinter.formulabase import FormulaBase
from sprinter.directory import Directory
from sprinter.exceptions import SprinterException
from sprinter.injections import Injections
from sprinter.manifest import Config, Manifest, ManifestException
from sprinter.system import System
from sprinter.pippuppet import Pip, PipException


def warmup(f):
    """ Decorator to run warmup before running a command """

    @wraps(f)
    def wrapped(self, *args, **kwargs):
        if not self.warmed_up:
            self._warmup()
        return f(self, *args, **kwargs)
    return wrapped


def install_required(f):
    """ Return an exception if the namespace is not already installed """

    @wraps(f)
    def wrapped(self, *args, **kwargs):
        if self.directory.new:
            raise SprinterException("Namespace %s is not yet installed!" % self.namespace)
        return f(self, *args, **kwargs)
    return wrapped

class Environment(object):

    source = None  # the path to the source handle, the handle itself, or a manifest instance
    target = None  # the path to the target handle, the handle itself, or a manifest instance
    namespace = None  # the namespace of the environment
    sprinter_namespace = None  # the namespace to make installs with. this affects:
    phase = None  # the phase currently running
    # the prefix added to injections
    # the libraries that environment utilizes
    config = None  # handles the configuration, and manifests
    directory = None  # handles interactions with the environment directory
    injections = None  # handles injections
    system = None  # stores utility methods to determine system specifics
    # variables typically populated programatically
    warmed_up = False  # returns true if the environment is ready for environments
    error_occured = False
    # a dictionary of the feature objects. 
    # The key is a tuple of feature name and formula, while the value is an instance.
    _feature_dict = {} 
    # a dictionary of the errors associated with features. 
    # The key is a tuple of feature name and formula, while the value is an instance.
    _error_dict = {}
    _errors = [] # list to keep all the errors
    # a pip puppet used to install eggs and add it to the classpath
    _pip = None
    sandboxes = []  # a list of package managers to sandbox (brew)

    def __init__(self, logger=None, logging_level=logging.INFO,
                 root=None, sprinter_namespace='sprinter'):
        self.system = System()
        if not logger:
            logger = self._build_logger(level=logging.INFO)
        self.logger = logger
        self.sprinter_namespace = sprinter_namespace
        self.root = root or os.path.expanduser(os.path.join("~", ".%s" % sprinter_namespace))
        self.global_path = os.path.join(self.root, ".global")
        self._pip = Pip(self.global_path)
        if logging_level == logging.DEBUG:
            self.logger.info("Starting in debug mode...")

    @warmup
    def install(self):
        """ Install the environment """
        self.phase = PHASE.INSTALL
        if not self.directory.new:
            self.logger.info("Namespace %s already exists!" % self.namespace)
            self.source = self.config.set_source(Manifest(self.directory.manifest_path))
            return self.update()
        try:
            self.logger.info("Installing environment %s..." % self.namespace)
            self.directory.initialize()
            self.install_sandboxes()
            self._instantiate_features()
            self._specialize_contexts()
            for feature in self._feature_dict.keys():
                self._run_action(feature, 'sync')
            self.inject_environment_rc()
            self._finalize()
        except Exception, e:
            self.logger.error("An error occured during installation!")
            self.clear_environment_rc()
            self.logger.info("Removing installation %s..." % self.namespace)
            self.directory.remove()
            raise e
        
    @warmup
    @install_required
    def update(self, reconfigure=False):
        """ update the environment """
        self.phase = PHASE.UPDATE
        self.logger.info("Updating environment %s..." % self.namespace)
        self.install_sandboxes()
        self._instantiate_features()
        for feature in self.config.updates():
            self.resolve_feature(feature)
        if reconfigure:
            self.config.grab_inputs(force_prompt=True)
        self._specialize_contexts()
        for feature in self._feature_dict.keys():
            self._run_action(feature, 'sync')
        self.inject_environment_rc()
        self._finalize()

    @warmup
    @install_required
    def remove(self):
        """ remove the environment """
        self.phase = "remove"
        self.logger.info("Removing environment %s..." % self.namespace)
        self._instantiate_features()
        self._specialize_contexts()
        for feature in self._feature_dict.keys():
            self._run_action(feature, 'sync')
        self.clear_environment_rc()
        self.directory.remove()
        self.injections.commit()

    @warmup
    @install_required
    def deactivate(self):
        """ deactivate the environment """
        self.phase = "deactivate"
        self.logger.info("Deactivating environment %s..." % self.namespace)
        self.directory.rewrite_rc = False
        self._instantiate_features()
        self._specialize_contexts()
        for feature in self._feature_dict.keys():
            self._run_action(feature, 'deactivate')
        self.clear_environment_rc()
        self._finalize()

    @warmup
    @install_required
    def activate(self):
        """ activate the environment """
        self.phase = "activate"
        self.logger.info("Activating environment %s..." % self.namespace)
        self.directory.rewrite_rc = False
        self._specialize_contexts()
        for feature in self._feature_dict.keys():
            self._run_action(feature, 'activate')
        self.inject_environment_rc()
        self._finalize()

    @warmup
    @install_required
    def reconfigure(self):
        """ reconfigure the environment """
        self.phase = "reconfigure"
        self.config.grab_inputs(force_prompt=True)
        if os.path.exists(self.directory.manifest_path):
            self.config.write(open(self.directory.manifest_path, "w+"))
        for feature in self.config.sections():
            self.reconfigure_feature(feature)
        self.logger.info("Reconfigured! Note: It's recommended to update after a configure")

    @warmup
    def validate_manifest(self, manifest):
        """ Validate a manifest object """
        invalidations = manifest.invalidations
        for s in self.target.formula_sections():
            invalidations += self.validate_feature(self, s)
        return invalidations

    @warmup
    def inject_environment_rc(self):
        # clearing profile for now, to make sure
        # profile injections are cleared for sprinter installs
        self.injections.clear("~/.profile")
        self.injections.inject("~/.bash_profile", "[ -d %s ] && . %s/.rc" %
                               (self.directory.root_dir, self.directory.root_dir))
        self.injections.inject("~/.bashrc", "[ -d %s ] && . %s/.rc" %
                               (self.directory.root_dir, self.directory.root_dir))

    @warmup
    def clear_environment_rc(self):
        self.injections.clear("~/.profile")
        self.injections.clear("~/.bash_profile")
        self.injections.clear("~/.bashrc")

    def install_sandboxes(self):
        if self.target:
            if self.system.isOSX():
                self._install_sandbox('brew', brew.install_brew)

    def write_debug_log(self, file_path):
        """ Write the debug log to a file """
        with open(file_path, "w+") as fh:
            fh.write(self._debug_stream.getvalue())
            fh.write("The following errors occured:\n")
            for error in self.errors:
                fh.write(error + "\n")
            for k, v in self._error_dict:
                if len(v) > 0:
                    fh.write("Error(s) in %s with formula %s:\n" % k)
                    for error in v:
                        fh.write(error + "\n")

    def message_failure(self):
        """ return a failure message, if one exists """
        manifest = self.target or self.source
        if manifest.has_option('config', 'message_failure'):
            return manifest.get('config', 'message_failure')
        return None

    def message_success(self):
        """ return a success message, if one exists """
        manifest = self.target or self.source
        if manifest.has_option('config', 'message_success'):
            return manifest.get('config', 'message_success')

    def _warmup(self):
        """ initialize variables necessary to perform a sprinter action """
        self.logger.debug("Warming up...")
        try:
            if not isinstance(self.source, Manifest) and self.source:
                self.source = Manifest(self.source)
            if not isinstance(self.target, Manifest) and self.target:
                self.target = Manifest(self.target)
        except lib.BadCredentialsException, e:
            self.logger.error(str(e))
            raise SprinterException("Fatal error! Bad credentials to grab manifest!")
        self.config = Config(source=self.source, target=self.target,
                             namespace=self.namespace)
        if not self.namespace:
            self.namespace = self.config.namespace
        if not self.directory:
            self.directory = Directory(self.namespace, sprinter_root=self.root)
        self.injections = Injections(wrapper="%s_%s" % (self.sprinter_namespace.upper(), self.namespace))
        # append the bin, in the case sandboxes are necessary to
        # execute commands further down the sprinter lifecycle
        os.environ['PATH'] = self.directory.bin_path() + ":" + os.environ['PATH']
        self.warmed_up = True

    def _instantiate_features(self):
        """ Create and instantiate the feature dictionary """
        for kind, manifest in [('source', self.source), 
                               ('target', self.target)]:
            for feature in manifest.formula_sections():
                feature_config = manifest.get_feature_config(feature)
                if feature_config.has('formula'):
                    key = (feature, config.get('formula')
                    if key not in self._feature_dict:
                        try:
                            formula_class = self._get_formula_class(config.get('formula'))
                            self._feature_dict[key] = formula_class(self, **{kind: feature_config})
                            self._error_dict[key] = []
                        except SprinterException:
                            self._log_error("Invalid formula %s for %s feature %s!" 
                                            % (config.get('formula'), kind, feature))
                    else:
                        setattr(self._feature_dict[key], kind, feature_config)
                else:
                    errors += ['source feature %s has no formula!' % feature]

    def _finalize(self):
        """ command to run at the end of sprinter's run """
        self.logger.info("Finalizing...")
        if os.path.exists(self.directory.manifest_path):
            self.config.write(open(self.directory.manifest_path, "w+"))
        if self.directory.rewrite_rc:
            self.directory.add_to_rc("export PATH=%s:$PATH" % self.directory.bin_path())
            self.directory.add_to_rc("export LIBRARY_PATH=%s:$LIBRARY_PATH" % self.directory.lib_path())
            self.directory.add_to_rc("export C_INCLUDE_PATH=%s:$C_INCLUDE_PATH" % self.directory.include_path())
        self.injections.commit()
        if self.message_success():
            self.logger.info(self.message_success())

    def _install_sandbox(self, name, call, kwargs={}):
        if (self.target.is_true('config', name) and
           (not self.source or not self.source.is_true('config', name))):
            self.logger.info("Installing %s..." % name)
            call(self.directory.root_dir, **kwargs)

    def _build_logger(self, level=logging.INFO):
        """ return a logger. if logger is none, generate a logger from stdout """
        self._debug_stream = StringIO()
        logger = logging.getLogger('sprinter')
        # stdout log
        out_hdlr = logging.StreamHandler(sys.stdout)
        out_hdlr.setLevel(level)
        logger.addHandler(out_hdlr)
        # debug log
        debug_hdlr = logging.StreamHandler(self._debug_stream)
        debug_hdlr.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
        debug_hdlr.setLevel(logging.DEBUG)
        logger.addHandler(debug_hdlr)
        logger.setLevel(logging.DEBUG)
        return logger

    def _get_formula_class(self, formula):
        """
        get a formula class object if it exists, else
        create one, add it to the dict, and pass return it.
        """
        try:
            return lib.get_subclass_from_module(formula, FormulaBase)
        except (SprinterException, ImportError):
            self.logger.info("Downloading %s..." % formula)
            try:
                self._pip.install_egg(formula)
            except PipException:
                self.logger.error("Unable to download %s!" % formula)
            return lib.get_subclass_from_module(formula, FormulaBase)

    def _log_error(self, error_message):
        self.error_occured = True
        self.errors += [error_message]
        self.logger.error(error_message)

    def _log_feature_error(self, feature, error_message):
        self.error_occured = True
        self._error_dict[feature] += [error_message]
        self.logger.error(error_message)
            
    def _run_action(self, feature, action, run_if_error=False):
        """ Run an action, and log it's output in case of errors """
        if len(self._error_dict[feature]) > 0 and not run_if_error:
            return
        instance = self._feature_dict[feature]
        try:
            self._error_dict[feature] += getattr(instance, action)()
            if len(self._error_dict[feature]) > 0:
                self.error_occured = True
        except Exception, e:
            self.log_feature_error(feature, str(e))

    def _specialize_contexts(self):
        """ Add variables and specialize contexts """
        # add in the 'root_dir' directories to the context dictionaries
        self.config.grab_inputs()
        for feature in self._feature_dict.keys():
            self._run_action(feature, 'validate', run_if_error=True)
            self._run_action(feature, 'prompt')
        for manifest in [self.source, self.target]:
            context_dict = {}
            if manifest:
                for s in manifest.formula_sections():
                    context_dict["%s:root_dir" % s] = self.directory.install_directory(s)
                    context_dict['config:root_dir'] = self.directory.root_dir
                    context_dict['config:node'] = self.system.node
                manifest.add_additional_context(context_dict)
