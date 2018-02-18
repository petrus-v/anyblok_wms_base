# -*- coding: utf-8 -*-
# This file is a part of the AnyBlok / WMS Base project
#
#    Copyright (C) 2018 Georges Racinet <gracinet@anybox.fr>
#
# This Source Code Form is subject to the terms of the Mozilla Public License,
# v. 2.0. If a copy of the MPL was not distributed with this file,You can
# obtain one at http://mozilla.org/MPL/2.0/.
from anyblok.tests.testcase import BlokTestCase
from anyblok_wms_base.constants import (
    SPLIT_AGGREGATE_PHYSICAL_BEHAVIOUR
    )
from anyblok_wms_base.exceptions import (
    OperationIrreversibleError,
    )


class TestSplit(BlokTestCase):
    """Specific testing of Wms.Operation.Split

    This may look very partial, but most of the testing of Split is
    actually done within the tests of operations that inherit from the
    GoodsSplitter mixin.

    TODO for the sake of clarity and granularity of tests, complete this
    testcase so that Split can have an independent development life.
    """

    def single_result(self, query):
        results = query.all()
        self.assertEqual(len(results), 1)
        return results[0]

    def setUp(self):
        Wms = self.registry.Wms
        self.Operation = Operation = Wms.Operation
        self.Goods = Wms.Goods
        self.goods_type = Wms.Goods.Type.insert(label="My good type")
        self.incoming_loc = Wms.Location.insert(label="Incoming location")
        self.stock = Wms.Location.insert(label="Stock")

        self.arrival = Operation.Arrival.insert(goods_type=self.goods_type,
                                                location=self.incoming_loc,
                                                state='planned',
                                                quantity=3)

        self.goods = self.Goods.insert(quantity=3,
                                       type=self.goods_type,
                                       location=self.incoming_loc,
                                       state='future',
                                       reason=self.arrival)

    def test_create_done(self):
        self.goods.state = 'present'
        split = self.Operation.Split.create(state='done',
                                            goods=self.goods,
                                            quantity=2)

        outcomes = self.Goods.query().filter(self.Goods.reason == split).all()
        self.assertEqual(len(outcomes), 2)
        self.assertTrue(self.goods in outcomes)
        self.assertEqual(self.goods.quantity, 1)
        self.assertEqual(sum(out.quantity for out in outcomes), 3)
        for outcome in outcomes:
            self.assertEqual(outcome.location, self.incoming_loc)
            self.assertEqual(outcome.type, self.goods_type)
            # I prefer this to the less explicit assertGreater (ambiguity
            # for French speakers with math background)
            self.assertTrue(outcome.quantity > 0)
            self.assertEqual(outcome.state, 'present')

    def test_irreversible(self):
        """A case in which Splits are irreversible."""
        self.goods_type.behaviours = {SPLIT_AGGREGATE_PHYSICAL_BEHAVIOUR: True}
        # if that fails, then the issue is not in Split implementation:
        self.assertEqual(self.goods_type.is_split_reversible(), False)

        self.goods.state = 'present'
        split = self.Operation.Split.create(state='done',
                                            goods=self.goods,
                                            quantity=2)
        with self.assertRaises(OperationIrreversibleError) as arc:
            split.plan_revert()
        exc = arc.exception
        str(exc)
        self.assertEqual(exc.kwargs.get('op'), split)

    def test_revert_final(self):
        """Test reversal of a Split that's the latest operation on goods.

        Starting point of the test is exactly the same as in
        :meth:`test_create_done`, we don't repeat assertions about that.
        """
        self.goods.state = 'present'
        split = self.Operation.Split.create(state='done',
                                            goods=self.goods,
                                            quantity=2)

        outcomes = self.Goods.query().filter(self.Goods.reason == split).all()
        split.plan_revert()

        aggregate = self.single_result(self.Operation.Aggregate.query())
        self.assertEqual(aggregate.goods, outcomes)
        aggregate.execute()

        new_goods = self.single_result(
            self.Goods.query().filter(self.Goods.state == 'present'))
        self.assertEqual(new_goods.quantity, 3)
        # TODO would be neat for the outcome to actually be self.goods,
        # i.e., the Goods record we started with
        self.assertEqual(new_goods.location, self.goods.location)
        self.assertEqual(new_goods.type, self.goods.type)
        self.assertEqual(new_goods.properties, self.goods.properties)

        # TODO we might actually want in case Splits have no meaning
        # in real life to simply forget an end-of-chain Split.
        self.assertEqual(self.Goods.query().filter(
            self.Goods.state == 'past').all(), outcomes)

        # no weird leftovers
        self.assertEqual(
            self.Goods.query().filter(self.Goods.quantity < 0).count(), 0)
        self.assertEqual(
            self.Goods.query().filter(self.Goods.state == 'future').count(), 0)

    def test_revert_implicit_intermediate(self):
        """Test reversal of a Split that's been inserted implictely.
        """
        self.goods.state = 'present'
        move = self.Operation.Move.create(state='done',
                                          destination=self.stock,
                                          goods=self.goods,
                                          quantity=2)

        self.assertEqual(len(move.follows), 1)
        split = move.follows[0]
        self.assertEqual(self.goods.reason, split)

        aggregate, rev_leafs = split.plan_revert()
        self.assertEqual(len(rev_leafs), 1)
        rev_move = rev_leafs[0]

        self.assertEqual(rev_move.follows, [move])
        rev_move.execute()
        aggregate.execute()

        new_goods = self.single_result(
            self.Goods.query().filter(self.Goods.state == 'present'))
        self.assertEqual(new_goods.quantity, 3)
        # TODO would be neat for the outcome to actually be self.goods,
        # i.e., the Goods record we started with
        self.assertEqual(new_goods.location, self.goods.location)
        self.assertEqual(new_goods.type, self.goods.type)
        self.assertEqual(new_goods.properties, self.goods.properties)

        # no weird leftovers
        self.assertEqual(
            self.Goods.query().filter(self.Goods.quantity < 0).count(), 0)
        self.assertEqual(
            self.Goods.query().filter(self.Goods.state == 'future').count(), 0)
