from __future__ import print_function, division, absolute_import

import logging
from distributed.utils import log_errors

from toolz import first
from tornado import gen
from tornado.ioloop import PeriodicCallback

logger = logging.getLogger(__file__)


class Adaptive(object):
    '''
    Adaptively allocate workers based on scheduler load.  A superclass.

    Contains logic to dynamically resize a Dask cluster based on current use.

    Parameters
    ----------
    scheduler: distributed.Scheduler
    cluster: object
        Must have scale_up and scale_down methods/coroutines

    Examples
    --------
    >>> class MyCluster(object):
    ...     def scale_up(self, n):
    ...         """ Bring worker count up to n """
    ...     def scale_down(self, workers):
    ...        """ Remove worker addresses from cluster """
    '''
    def __init__(self, scheduler=None, cluster=None, interval=1000, startup_cost=1):
        self.cluster = cluster
        if scheduler is None:
            scheduler = cluster.scheduler
        self.scheduler = scheduler
        self.startup_cost = startup_cost
        self._adapt_callback = PeriodicCallback(self._adapt, interval,
                                                self.scheduler.loop)
        self.scheduler.loop.add_callback(self._adapt_callback.start)
        self._adapting = False

    @gen.coroutine
    def _retire_workers(self):
        """
        Get the cluster scheduler to cleanup any workers it decides can retire
        """
        with log_errors():
            workers = yield self.scheduler.retire_workers(remove=True, close=True)
            logger.info("Retiring workers {}".format(workers))

    @gen.coroutine
    def _adapt(self):
        logger.info("Adapting")
        with log_errors():
            if self._adapting:  # Semaphore to avoid overlapping adapt calls
                return

            s = self.scheduler

            self._adapting = True
            try:
                if s.unrunnable:
                    key = first(s.unrunnable)
                    memory = s.resource_restrictions[key]['memory']

                    #  We need a worker with more resources. See if one has already been requested.
                    for worker, resources in self.cluster.workers.items():
                        if (resources.get("memory", 0) >= memory * 2 and
                            self.cluster.session.jobStatus(worker) in ('running', 'queued_active')):
                                #There is already an existing valid worker requested with the necessary
                                #  resources to run this task. If the worker has any other status (like DONE, HOLD, etc.), scheduler another task.
                                break
                    else:
                        logger.info("Starting worker for unrunnable {}".format(key))
                        self.cluster.start_workers(1, memory=memory * 2)

                yield self._retire_workers()
            finally:
                self._adapting = False

    def adapt(self):
        self.scheduler.loop.add_callback(self._adapt)
