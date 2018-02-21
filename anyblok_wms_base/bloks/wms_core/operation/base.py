# -*- coding: utf-8 -*-
# This file is a part of the AnyBlok / WMS Base project
#
#    Copyright (C) 2018 Georges Racinet <gracinet@anybox.fr>
#
# This Source Code Form is subject to the terms of the Mozilla Public License,
# v. 2.0. If a copy of the MPL was not distributed with this file,You can
# obtain one at http://mozilla.org/MPL/2.0/.

import logging
from datetime import datetime

from anyblok import Declarations
from anyblok.column import String
from anyblok.column import Selection
from anyblok.column import Integer
from anyblok.column import DateTime
from anyblok.relationship import Many2Many

from anyblok_wms_base.constants import OPERATION_STATES, OPERATION_TYPES
from anyblok_wms_base.exceptions import (
    OperationCreateArgFollows,
    OperationError,
    OperationIrreversibleError,
    )

logger = logging.getLogger(__name__)
register = Declarations.register
Model = Declarations.Model


@register(Model.Wms)
class Operation:
    """A stock operation.

    .. warning:: downstream applications and libraries should never issue
                 :meth:`insert` for Operations in their main code, and must use
                 :meth:`create` instead. See the documention of :meth:`create`
                 for more about this.

    The Operation model encodes the common part of all precise operations,
    which themselves have dedicated models. This is implemented with the
    polymorphic features of SQLAlchemy and AnyBlok.

    The main purpose of this separation is to simplify auditing purposes: the
    Goods model can bear a ``reason`` column, operations can be linked whatever
    their types are.

    Downstream applications and libraries can add columns on the present model
    to satisfy their auditing needs (some notion of "user" or "operator" comes
    to mind).

    Fields semantics:

    - id: is equal to the id of the concrete operations model
    - state: see :ref:`op_states`
    - comment:
        free field to store details of how it went, or motivation
        for the operation (downstream libraries implementing scheduling
        should better use columns rather than this field).
    - follows:
        the operations that are the direct reasons
        for the presence of Goods the present one is about.
        This is a Many2Many relationship because there might be
        several Goods involved in the operation, but for each of them,
        it'll be exactly one operation, namely the latest before
        the present one. In other words, operations history is a directed
        acyclic graph, whose edges are encoded by this Many2Many.

        This field can be empty in case of initial operations.

        Examples:

        + a move of a bottle of milk that follows the unpacking
          of a 6-pack, which itself follows a move from somewhere else
        + a parcel packing operation that follows exactly one move
          to the shipping area for each Goods to be packed.
          they themselves would follow more operations.
        + an :class:`Arrival <.arrival.Arrival>` typically doesn't
          follow anything (but might be due to some kind of purchase order).
    """
    id = Integer(label="Identifier, shared with specific tables",
                 primary_key=True)
    # TODO enums ?
    type = Selection(label="Operation Type",
                     selections=OPERATION_TYPES,
                     nullable=False,
                     )
    state = Selection(label="State of operation",
                      selections=OPERATION_STATES,
                      nullable=False,
                      )
    comment = String(label="Comment")
    follows = Many2Many(model='Model.Wms.Operation',
                        m2m_remote_columns='parent_id',
                        m2m_local_columns='child_id',
                        join_table='wms_operation_history',
                        label="Immediate preceding operations",
                        many2many="followers",
                        )
    dt_execution = DateTime(label="date and time of execution",
                            nullable=False)
    """Date and time of execution.

    For Operations in state ``done``, this represents the time at which the
    Operation has been completed, i.e., has reached that state.

    For Operations in states ``planned`` and ``started``,
    this represents the time at which the execution is supposed to complete.
    This has consequences on the :attr:`dt_from
    <anyblok_wms_base.bloks.wms_core.goods.Goods.dt_from>` and :attr:`dt_until
    <anyblok_wms_base.bloks.wms_core.goods.Goods.dt_until>` fields of
    the Goods affected by this Operation, to avoid summing up
    records representing in the same physical goods while :meth:`peeking at
    quantities in the future
    <anyblok_wms_base.bloks.wms_core.location.Location.quantity>`,
    but has no other strong meaning within
    ``wms-core``: if the end application does some serious time prediction,
    it can use it about freely. The actual execution can
    occur later at any time, and :meth:`execute` will in particular
    correct the value of this field, and its consequences on the affected
    Goods.
    """

    dt_start = DateTime(label="date and time of start")
    """Date and time of start, if different than execution.

    For all Operations, if the value of this field is None, this means that
    the Operation is considered to be instantaneous.

    Overall, ``wms-core`` doesn't do much about this field other than recording
    it: end applications that perform does serious time predictions can use
    it about freely.

    .. note:: We will probably later on make use of this field in
              destructive Operations to update the :attr:`dt_until
              <anyblok_wms_base.bloks.wms_core.goods.Goods.dt_until>` field
              of their incoming Goods, meaning that they won't appear in
              present quantity queries anymore.

    For Operations in states ``done`` and ``started`` this represents the time
    at which the Operation has been started, i.e., has reached the ``started``
    state.

    For Operations in state ``planned``, this is as much theoretical as the
    field ``dt_execution`` is.
    """

    @classmethod
    def define_mapper_args(cls):
        mapper_args = super(Operation, cls).define_mapper_args()
        if cls.__registry_name__ == 'Model.Wms.Operation':
            mapper_args.update({
                'polymorphic_identity': 'operation',
                'polymorphic_on': cls.type,
            })
        else:
            mapper_args.update({
                'polymorphic_identity': cls.TYPE,
            })

        return mapper_args

    def __repr__(self):
        return ("{model_name}(id={self.id}, state={self.state!r}, "
                "{specific})").format(self=self,
                                      model_name=self.__registry_name__,
                                      specific=self.specific_repr())

    __str__ = __repr__

    @classmethod
    def forbid_follows_in_create(cls, follows, kwargs):
        if follows is not None:
            raise OperationCreateArgFollows(cls, kwargs)

    @classmethod
    def create(cls, state='planned', follows=None,
               dt_execution=None, dt_start=None, **fields):
        """Main method for creation of operations

        In contrast with :meth:`insert`, this class method performs
        some Wms specific logic,
        e.g, creation of Goods, but that's up to the specific subclasses.

        :param state:
           value of the :attr:`state` field right after creation. It has
           a strong influence on the consequences of the Operation:
           creating an Operation in the ``done`` state means executing it
           right away.

           Creating an Oparation in the ``started`` state should
           make the relevant Goods locked or destroyed right away
           (TODO Not implemented)
        :param fields: remaining fields, to be forwarded to ``insert`` and
                       the various involved methods implemented in subclasses.
        :param dt_execution:
           value of the attr:`dt_execution` right at creation. If
           ``state==planned``, this is mandatory. Otherwise, it defaults to
           the current date and time (:meth:`datetime.now`)
        :param follows:
           singled out to forbid to pass it in ``kwargs``
        :return Operation: concrete instance of the appropriate Operation
                           subclass.

        In principle, downstream developers should never call :meth:`insert`.

        As this is Python, nothing really forbids them for doing so, but they
        must then exactly know what they are doing, much like issuing
        INSERT statements to the database).

        Keeping :meth:`insert` as in vanilla SQLAlchemy has the advantage of
        making them easily usable in
        ``wms_core`` internal implementation without side effects.

        On the other hand, downstream developers should feel free to
        :meth:`insert` and :meth:`update` in their unit or integration tests.
        The fact that they are inert should help reproduce weird situations
        (yes, the same could be achieved by forcing to use the Model class
        methods instead).
        """
        cls.forbid_follows_in_create(follows, fields)
        if dt_execution is None:
            if state == 'done':
                dt_execution = datetime.now()
            else:
                raise OperationError(
                    cls,
                    "Creation in state {state!r} requires the "
                    "'dt_execution' field (date and time when "
                    "it's supposed to be done).",
                    state=state)
        cls.check_create_conditions(state, dt_execution, **fields)
        follows = cls.find_parent_operations(**fields)
        op = cls.insert(state=state, dt_execution=dt_execution, **fields)
        op.follows.extend(follows)
        op.after_insert()
        return op

    def execute(self, dt_execution=None):
        """Execute the operation.

        :param datetime dt_execution:
           the time at which execution happens.
           This parameter is meant for tests, or for callers doing bulk
           execution. If omitted, it defaults to the current date
           and time (:meth:`datetime.now`)

        This is an idempotent call: if the operation is already done,
        nothing happens.
        """
        if self.state == 'done':
            return
        if dt_execution is None:
            dt_execution = datetime.now()
        self.dt_execution = dt_execution
        if self.dt_start is None:
            self.dt_start = dt_execution
        self.check_execute_conditions()
        self.execute_planned()
        self.state = 'done'

    def cancel(self):
        """Cancel a planned operation and all its consequences.

        This method will recursively cancel all follow-ups of ``self``, before
        cancelling ``self`` itself.

        The implementation is for now a simple recursion, and hence can
        lead to :class:`RecursionError` on huge graphs.
        TODO rewrite using an accumulation logic rather than recursion.
        """
        if self.state != 'planned':
            raise OperationError(
                self,
                "Can't cancel {op} because its state {op.state!r} is not "
                "'planned'", op=self)
        logger.debug("Cancelling operation %r", self)

        # followers attribute value will mutate during the loop
        followers = tuple(self.followers)
        for follower in followers:
            follower.cancel()
        self.cancel_single()
        self.follows.clear()
        self.delete()
        logger.info("Cancelled operation %r", self)

    def is_reversible(self):
        """Tell whether the current operation can be in principle reverted.

        This does not check that actual conditions to plan a revert are met
        (which would need to plan reversals for all followers first),
        but only that, in principle, it is possible.

        :return: the answer
        :rtype: bool

        As there are many irreversible operations, and besides, reversibility
        has to be implemented for each subclass, the default implementation
        returns ``False``. Subclasses implementing reversibility have to
        override this.

        """
        return False

    def plan_revert(self, dt_execution=None):
        """Plan operations to revert the present one and its consequences.

        Like :meth:`cancel`, this method is recursive, but it applies only
        to operations that are in the 'done' state.

        It is expected that some operations can't be reverted, because they
        are destructive, and in that case an exception will be raised.

        For now, time handling is rather dumb, as it will plan
        all the operations at the same date and time (this Blok has to idea
        of operations lead times), but that shouldn't be a problem.

        :param datetime dt_execution:
           the time at which to plan the reversal operations.
           If not supplied, the current date and time will be used.

        :rtype: (Operation, list(Operation))
        :return: the operation reverting the present one, and
                 the list of initial operations to be executed to actually
                 start reversing the whole.
        """
        if dt_execution is None:
            dt_execution = datetime.now()
        if self.state != 'done':
            # TODO actually it'd be nice to cancel or update
            # planned operations (think of reverting a Move meant for
            # organisation, but keeping an Unpack that was scheduled
            # afterwards)
            raise OperationError(
                self,
                "Can't plan reversal of {op} because "
                "its state {op.state!r} is not 'done'", op=self)
        if not self.is_reversible():
            raise OperationIrreversibleError(self)

        logger.debug("Planning reversal of operation %r", self)

        exec_leafs = []
        followers_reverts = []
        for follower in self.followers:
            follower_revert, follower_exec_leafs = follower.plan_revert()
            self.registry.flush()
            followers_reverts.append(follower_revert)
            exec_leafs.extend(follower_exec_leafs)
        this_reversal = self.plan_revert_single(dt_execution,
                                                follows=followers_reverts)
        self.registry.flush()
        if not exec_leafs:
            exec_leafs.append(this_reversal)
        logger.info("Planned reversal of operation %r. "
                    "Execution starts with %r", self, exec_leafs)
        return this_reversal, exec_leafs

    def obliviate(self):
        """Totally forget about an executed Operation and all its consequences.

        This is intended for cases where an Operation has been recorded by
        mistake (bug or human error), but did not happen at all in reality.

        We chose the word "obliviate" because it has a stronger feeling that
        simply "forget" and also sound more specific.

        This is not to be confused with reversals, which try and create a
        chain of Operations to perform to revert the effect of some Operations.

        If one reverts a Move that has been done by mistake,
        that means one performs a Move back (takes some time, can go wrong).
        If one obliviates a Move, that means one
        acknowledges that the Move never happened: its mere existence in the
        database is itself the mistake.

        Also, some Operations cannot be reverted in reality, whereas oblivion
        in our sense have no effect on reality.

        This method will recursively obliviate all follow-ups of ``self``,
        before ``self`` itself.

        The implementation is for now a simple recursion, and hence can
        lead to :class:`RecursionError` on huge graphs.
        TODO rewrite using an accumulation logic rather than recursion.
        TODO it is also very much a duplication of :meth:`cancel`. The
        recursion logic itself should probably be factorized in a common
        method.

        TODO For the time being, the implementation insists on all Operations
        to be in the ``done`` state, but it should probably accept those
        that are in the ``planned`` state, and call :meth:`cancel` on them,
        maybe this could become an option if we can't decide.
        """
        if self.state != 'done':
            raise OperationError(
                self,
                "Can't obliviate {op} because its state {op.state!r} is not "
                "'obliviate'", op=self)
        logger.debug("Obliviating operation %r", self)

        # followers attribute value will mutate during the loop
        followers = tuple(self.followers)
        for follower in followers:
            follower.obliviate()
        self.obliviate_single()
        self.follows.clear()

        self.delete()
        logger.info("Obliviated operation %r", self)

    @classmethod
    def check_create_conditions(cls, state, dt_execution, **kwargs):
        """Used during creation to check that the Operation is indeed doable.

        The given state obviously plays a role, and this pre insertion check
        can spare us some costly operations.

        :param dt_execution: the date and time at which the Operation is
                             (supposed to be) done.

        To be implemented in subclasses, by raising exceptions if something's
        wrong.
        """
        raise NotImplementedError  # pragma: no cover

    @classmethod
    def find_parent_operations(cls, **kwargs):
        """Return the list or tuple of operations that this one follows

        To be implemented in subclasses.
        """
        raise NotImplementedError  # pragma: no cover

    def after_insert(self):
        """Perform specific logic after insert during creation process

        To be implemented in subclasses.
        """
        raise NotImplementedError  # pragma: no cover

    @classmethod
    def check_execute_conditions(cls, **kwargs):
        """Used during execution to check that the Operation is indeed doable.

        To be implemented in subclasses, by raising an exception if something's
        wrong.
        """
        raise NotImplementedError  # pragma: no cover

    def execute_planned(self):
        """Execute an operation that has been up to now in the 'planned' state.

        To be implemented in subclasses.

        This method does not have to care about the Operation state, which
        the base class has already checked.

        This method must correct the dates and times on the affected Goods or
        more broadly of any consequences of the theoretical execution date
        and time that has been set during planning.
        For that purpose, it can rely on the value of the :attr:`dt_execution`
        field to be now the final one (can be sooner or later than expected).

        Normally, this method should not need either to perform any checks that
        the execution can, indeed, be done: such subclasses-specific
        tests are supposed to be done within :meth:`check_execute_conditions`.

        Downstream applications and libraries are
        not supposed to call this method: they should use :meth:`execute`,
        which takes care of all the above-mentioned preparations.
        """
        raise NotImplementedError  # pragma: no cover

    def cancel_single(self):
        """Cancel just the current operation.

        This method assumes that follow-up operations are already been
        taken care of. It removes all planned consequences of the operation,
        without deleting the operation itself.

        Downstream applications and libraries are
        not supposed to call this method: they should use :meth:`cancel`,
        which takes care of the necessary recursivity and the final deletion.

        To be implemented in sublasses
        """
        raise NotImplementedError(
            "for %s" % self.__registry_name__)  # pragma: no cover

    def obliviate_single(self):
        """Oblivate just the current operation.

        This method assumes that follow-up operations are already been
        taken care of. It removes all consequences of the operation,
        without deleting the operation itself.

        Downstream applications and libraries are
        not supposed to call this method: they should use :meth:`obliviate`,
        which takes care of the necessary recursivity and the final deletion.

        To be implemented in sublasses
        """
        raise NotImplementedError(
            "for %s" % self.__registry_name__)  # pragma: no cover

    def plan_revert_single(self, dt_execution):
        """Create a planned operation to revert the present one.

        :param datetime dt_execution: the date and time at which to plan
                                 the reversal operations.

        This method assumes that follow-up operations have already been taken
        care of.

        Downstream applications and libraries are
        not supposed to call this method: they should use :meth:`plan_revert`,
        which takes care of the necessary recursivity.

        To be implemented in sublasses
        """
        raise NotImplementedError(
            "for %s" % self.__registry_name__)  # pragma: no cover
