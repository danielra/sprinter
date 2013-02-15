"""
Unpacks and deploys to a location
"""

import gzip
import os
import tarfile
import urllib
from StringIO import StringIO

from sprinter.recipebase import RecipeBase


class UnpackRecipe(RecipeBase):
    """ A sprinter recipe for unpacking a compressed package and extracting it"""

    def setup(self, directory, feature_name, config):
        if config['type'] == "tar.gz":
            self.__extract_targz(config['url'], directory.install_directory(feature_name))
        if 'executable' in config:
            symlink_target = config['symlink'] if 'symlink' in config else config['executable']
            self.__symlink_executable(directory, feature_name, config['executable'], symlink_target)

    def update(self, directory, feature_name, old_config):
        pass

    def destroy(self, directory, feature_name, old_config):
        pass

    def __extract_targz(self, url, target_dir):
        """ extract a targz and install to the target directory """
        gz = gzip.GzipFile(fileobj=StringIO(urllib.urlopen(url).read()))
        tf = tarfile.TarFile(fileobj=gz)
        if not os.path.exists(target_dir):
            os.makedirs(target_dir)
        tf.extractall(path=target_dir)

    def __symlink_executable(self, directory, feature_name, source, target):
        source_path = os.path.join(directory.install_directory(feature_name), source)
        directory.symlink_to_bin(target, source_path)
