"""
Tests for directory class
"""
import os
import shutil
import tempfile

from nose import tools
from sprinter.directory import Directory, DirectoryException


class TestDirectory(object):
    """
    Test the directory object
    """

    def setup(self):
        self.temp_dir = tempfile.mkdtemp()
        self.directory = Directory('test', rewrite_rc=True,
                                   sprinter_root=self.temp_dir)
        self.directory.initialize()

    def teardown(self):
        if hasattr(self, 'directory'):
            del(self.directory)
        shutil.rmtree(self.temp_dir)

    def test_initialize(self):
        """ The initialize method should generate the proper directories """
        self.directory.initialize()
        assert not self.directory.new,\
            "new variable should be set to false for existing directory!"
        assert os.path.exists(self.directory.bin_path()),\
                              "bin directory should exist after initialize!"
        assert os.path.exists(self.directory.lib_path()),\
                              "lib directory should exist after initialize!"

    def test_initialize_new(self):
        """ The initialize method should return new for a non-existent directory """
        new_temp_dir = self.temp_dir + "e09dia0d"
        directory = Directory('test', rewrite_rc=False, sprinter_root=new_temp_dir)
        assert directory.new
        try:
            directory.initialize()
            assert not directory.new, "directory should not be new after initialization"
        finally:
            if os.path.exists(new_temp_dir):
                shutil.rmtree(new_temp_dir)

    def test_symlink_to_bin(self):
        """ symlink to bin should symlink to the bin sprinter environment folder """
        _, temp_file_path = tempfile.mkstemp()
        try:
            with open(temp_file_path, 'w+') as temp_file:
                temp_file.write('hobo')
            self.directory.symlink_to_bin('newfile', temp_file_path)
            assert os.path.islink(os.path.join(self.directory.bin_path(), 'newfile'))
            tools.eq_(open(os.path.join(self.directory.bin_path(), 'newfile')).read(),
                      open(temp_file_path).read(),
                      "File contents are different for symlinked files!")
            assert os.access(os.path.join(self.directory.bin_path(), 'newfile'), os.X_OK),\
                "File is not executable!"
        finally:
            os.unlink(temp_file_path)

    def test_symlink_to_lib(self):
        """ symlink to lib should symlink to the lib sprinter environment folder """
        _, temp_file = tempfile.mkstemp()
        with open(temp_file, 'w+') as tfh:
            tfh.write('hobo')
        self.directory.symlink_to_lib('newfile', temp_file)
        assert os.path.islink(os.path.join(self.directory.lib_path(), 'newfile'))
        tools.eq_(open(os.path.join(self.directory.lib_path(), 'newfile')).read(),
                  open(temp_file).read(),
                  "File contents are different for symlinked files!")
        
    def test_add_to_rc(self):
        """ Test if the add_to_rc method adds to the rc """
        test_content = "THIS IS AN OOOGA BOOGA TEST "
        self.directory.add_to_rc(test_content)
        rc_file_path = os.path.join(self.directory.root_dir, ".rc")
        del(self.directory)
        assert open(rc_file_path).read().find(test_content) != -1,\
            "test content was not found!"
        
    @tools.raises(DirectoryException)
    def test_add_to_rc_norc_rewrite(self):
        """
        With the rc_rewrite flag false, an exception should be thrown if
        one attempts to write to it
        """
        directory = Directory('test', rewrite_rc=False,
                              sprinter_root=self.temp_dir)
        directory.add_to_rc("test")

    def test_remove(self):
        """ Remove should remove the environment directory """
        self.directory.remove()
        assert not os.path.exists(self.directory.root_dir), "Path still exists after remove!"
