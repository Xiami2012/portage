# Copyright 2013 Gentoo Foundation
# Distributed under the terms of the GNU General Public License v2

import textwrap

import portage
from portage import os
from portage.tests import TestCase
from portage.tests.resolver.ResolverPlayground import ResolverPlayground
from portage.package.ebuild._ipc.QueryCommand import QueryCommand
from portage.util._async.ForkProcess import ForkProcess
from portage.util._async.TaskScheduler import TaskScheduler
from portage.util._eventloop.global_event_loop import global_event_loop
from _emerge.Package import Package
from _emerge.PipeReader import PipeReader

class DoebuildProcess(ForkProcess):

	__slots__ = ('doebuild_kwargs', 'doebuild_pargs')

	def _run(self):
		return portage.doebuild(*self.doebuild_pargs, **self.doebuild_kwargs)

class DoebuildFdPipesTestCase(TestCase):

	def testDoebuild(self):
		"""
		Invoke portage.doebuild() with the fd_pipes parameter, and
		check that the expected output appears in the pipe. This
		functionality is not used by portage internally, but it is
		supported for API consumers (see bug #475812).
		"""

		ebuild_body = textwrap.dedent("""
			S=${WORKDIR}
			pkg_info() { echo info ; }
			pkg_nofetch() { echo nofetch ; }
			pkg_pretend() { echo pretend ; }
			pkg_setup() { echo setup ; }
			src_unpack() { echo unpack ; }
			src_prepare() { echo prepare ; }
			src_configure() { echo configure ; }
			src_compile() { echo compile ; }
			src_test() { echo test ; }
			src_install() { echo install ; }
		""")

		ebuilds = {
			'app-misct/foo-1': {
				'EAPI'      : '5',
				"MISC_CONTENT": ebuild_body,
			}
		}

		playground = ResolverPlayground(ebuilds=ebuilds)
		try:
			QueryCommand._db = playground.trees
			root_config = playground.trees[playground.eroot]['root_config']
			portdb = root_config.trees["porttree"].dbapi
			settings = portage.config(clone=playground.settings)
			if "__PORTAGE_TEST_HARDLINK_LOCKS" in os.environ:
				settings["__PORTAGE_TEST_HARDLINK_LOCKS"] = \
					os.environ["__PORTAGE_TEST_HARDLINK_LOCKS"]
				settings.backup_changes("__PORTAGE_TEST_HARDLINK_LOCKS")

			settings.features.add("noauto")
			settings.features.add("test")
			settings['PORTAGE_PYTHON'] = portage._python_interpreter
			settings['PORTAGE_QUIET'] = "1"

			cpv = 'app-misct/foo-1'
			metadata = dict(zip(Package.metadata_keys,
				portdb.aux_get(cpv, Package.metadata_keys)))

			pkg = Package(built=False, cpv=cpv, installed=False,
				metadata=metadata, root_config=root_config,
				type_name='ebuild')
			settings.setcpv(pkg)
			ebuildpath = portdb.findname(cpv)
			self.assertNotEqual(ebuildpath, None)

			for phase in ('info', 'nofetch',
				 'pretend', 'setup', 'unpack', 'prepare', 'configure',
				 'compile', 'test', 'install', 'clean', 'merge'):

				pr, pw = os.pipe()

				producer = DoebuildProcess(doebuild_pargs=(ebuildpath, phase),
					doebuild_kwargs={"settings" : settings,
						"mydbapi": portdb, "tree": "porttree",
						"vartree": root_config.trees["vartree"],
						"fd_pipes": {1: pw, 2: pw},
						"prev_mtimes": {}})

				consumer = PipeReader(
					input_files={"producer" : pr})

				task_scheduler = TaskScheduler(iter([producer, consumer]),
					max_jobs=2)

				try:
					task_scheduler.start()
				finally:
					# PipeReader closes pr
					os.close(pw)

				task_scheduler.wait()
				output = portage._unicode_decode(
					consumer.getvalue()).rstrip("\n")

				if task_scheduler.returncode != os.EX_OK:
					portage.writemsg(output, noiselevel=-1)

				self.assertEqual(task_scheduler.returncode, os.EX_OK)

				if phase not in ('clean', 'merge'):
					self.assertEqual(phase, output)

		finally:
			playground.cleanup()
			QueryCommand._db = None
