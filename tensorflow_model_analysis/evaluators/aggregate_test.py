# Copyright 2018 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Test for using the Aggregate API."""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
# Standard Imports

import apache_beam as beam
from apache_beam.testing import util
import numpy as np
import tensorflow as tf
from tensorflow_model_analysis import constants
from tensorflow_model_analysis import types
from tensorflow_model_analysis.eval_saved_model import load
from tensorflow_model_analysis.eval_saved_model import testutil
from tensorflow_model_analysis.eval_saved_model.example_trainers import linear_classifier
from tensorflow_model_analysis.evaluators import aggregate


def create_test_input(predict_list, slice_list):
  results = []
  for entry in predict_list:
    for slice_key in slice_list:
      results.append((slice_key, {
          constants.FEATURES_PREDICTIONS_LABELS_KEY: entry
      }))
  return results


class AggregateTest(testutil.TensorflowModelAnalysisTest):

  def _getEvalExportDir(self):
    return os.path.join(self._getTempDir(), 'eval_export_dir')

  def testCalculateConfidenceInterval(self):
    sampling_data_list = [
        np.array([
            [0, 0, 2, 7, 0.77777779, 1],
            [1, 0, 2, 6, 0.75, 0.85714287],
            [4, 0, 2, 3, 0.60000002, 0.42857143],
            [4, 2, 0, 3, 1, 0.42857143],
            [7, 2, 0, 0, float('nan'), 0],
        ]),
        np.array([
            [7, 2, 0, 0, float('nan'), 0],
            [0, 0, 2, 7, 0.77777779, 1],
            [1, 0, 2, 6, 0.75, 0.85714287],
            [4, 0, 2, 3, 0.60000002, 0.42857143],
            [4, 2, 0, 3, 1, 0.42857143],
        ]),
    ]
    unsampled_data = np.array([
        [4, 2, 0, 3, 1, 0.42857143],
        [7, 2, 0, 0, float('nan'), 0],
        [0, 0, 2, 7, 0.77777779, 1],
        [1, 0, 2, 6, 0.75, 0.85714287],
        [4, 0, 2, 3, 0.60000002, 0.42857143],
    ])
    result = aggregate._calculate_t_distribution(sampling_data_list,
                                                 unsampled_data)
    self.assertIsInstance(result, np.ndarray)
    self.assertEqual(result.shape, (5, 6))
    self.assertAlmostEqual(result[0][0].sample_mean, 3.5, delta=0.1)
    self.assertAlmostEqual(
        result[0][0].sample_standard_deviation, 4.94, delta=0.1)
    self.assertEqual(result[0][0].sample_degrees_of_freedom, 1)
    self.assertEqual(result[0][0].unsampled_value, 4.0)
    self.assertAlmostEqual(result[0][4].sample_mean, 0.77, delta=0.1)
    self.assertTrue(np.isnan(result[0][4].sample_standard_deviation))
    self.assertEqual(result[0][4].sample_degrees_of_freedom, 0)
    self.assertEqual(result[0][4].unsampled_value, 1.0)

    sampling_data_list = [
        np.array([1, 2]),
        np.array([1, 2]),
        np.array([1, float('nan')])
    ]
    unsampled_data = np.array([1, 2])
    result = aggregate._calculate_t_distribution(sampling_data_list,
                                                 unsampled_data)
    self.assertIsInstance(result, np.ndarray)
    self.assertEqual(result.tolist(), [
        types.ValueWithTDistribution(
            sample_mean=1.0,
            sample_standard_deviation=0.0,
            sample_degrees_of_freedom=2,
            unsampled_value=1),
        types.ValueWithTDistribution(
            sample_mean=2.0,
            sample_standard_deviation=0.0,
            sample_degrees_of_freedom=1,
            unsampled_value=2)
    ])

  def testAggregateOverallSlice(self):

    temp_eval_export_dir = self._getEvalExportDir()
    _, eval_export_dir = linear_classifier.simple_linear_classifier(
        None, temp_eval_export_dir)

    eval_saved_model = load.EvalSavedModel(eval_export_dir)
    eval_shared_model = self.createTestEvalSharedModel(
        eval_saved_model_path=eval_export_dir)

    with beam.Pipeline() as pipeline:
      example1 = self._makeExample(age=3.0, language='english', label=1.0)
      example2 = self._makeExample(age=3.0, language='chinese', label=0.0)
      example3 = self._makeExample(age=4.0, language='english', label=1.0)
      example4 = self._makeExample(age=5.0, language='chinese', label=0.0)

      predict_result = eval_saved_model.as_features_predictions_labels(
          eval_saved_model.predict_list([
              example1.SerializeToString(),
              example2.SerializeToString(),
              example3.SerializeToString(),
              example4.SerializeToString()
          ]))

      metrics, _ = (
          pipeline
          | 'CreateTestInput' >> beam.Create(
              create_test_input(predict_result, [()]))
          | 'ComputePerSliceMetrics' >> aggregate.ComputePerSliceMetrics(
              eval_shared_model=eval_shared_model, desired_batch_size=3))

      def check_result(got):
        self.assertEqual(1, len(got), 'got: %s' % got)
        slice_key, metrics = got[0]
        self.assertEqual(slice_key, ())
        self.assertDictElementsAlmostEqual(
            metrics, {
                'accuracy': 1.0,
                'label/mean': 0.5,
                'my_mean_age': 3.75,
                'my_mean_age_times_label': 1.75,
            })

      util.assert_that(metrics, check_result)

  def testAggregateMultipleSlices(self):
    temp_eval_export_dir = self._getEvalExportDir()
    _, eval_export_dir = linear_classifier.simple_linear_classifier(
        None, temp_eval_export_dir)

    eval_saved_model = load.EvalSavedModel(eval_export_dir)
    eval_shared_model = self.createTestEvalSharedModel(
        eval_saved_model_path=eval_export_dir)

    with beam.Pipeline() as pipeline:
      example1 = self._makeExample(age=3.0, language='english', label=1.0)
      example2 = self._makeExample(age=3.0, language='chinese', label=0.0)
      example3 = self._makeExample(age=4.0, language='english', label=1.0)
      example4 = self._makeExample(age=5.0, language='chinese', label=0.0)

      predict_result_english_slice = (
          eval_saved_model.as_features_predictions_labels(
              eval_saved_model.predict_list(
                  [example1.SerializeToString(),
                   example3.SerializeToString()])))

      predict_result_chinese_slice = (
          eval_saved_model.as_features_predictions_labels(
              eval_saved_model.predict_list(
                  [example2.SerializeToString(),
                   example4.SerializeToString()])))

      test_input = (
          create_test_input(predict_result_english_slice, [(
              ('language', 'english'))]) +
          create_test_input(predict_result_chinese_slice, [(
              ('language', 'chinese'))]) +
          # Overall slice
          create_test_input(
              predict_result_english_slice + predict_result_chinese_slice,
              [()]))

      metrics, _ = (
          pipeline
          | 'CreateTestInput' >> beam.Create(test_input)
          | 'ComputePerSliceMetrics' >> aggregate.ComputePerSliceMetrics(
              eval_shared_model=eval_shared_model, desired_batch_size=3))

      def check_result(got):
        self.assertEqual(3, len(got), 'got: %s' % got)
        slices = {}
        for slice_key, metrics in got:
          slices[slice_key] = metrics
        overall_slice = ()
        english_slice = (('language', 'english'))
        chinese_slice = (('language', 'chinese'))
        self.assertItemsEqual(
            list(slices.keys()), [overall_slice, english_slice, chinese_slice])
        self.assertDictElementsAlmostEqual(
            slices[overall_slice], {
                'accuracy': 1.0,
                'label/mean': 0.5,
                'my_mean_age': 3.75,
                'my_mean_age_times_label': 1.75,
            })
        self.assertDictElementsAlmostEqual(
            slices[english_slice], {
                'accuracy': 1.0,
                'label/mean': 1.0,
                'my_mean_age': 3.5,
                'my_mean_age_times_label': 3.5,
            })
        self.assertDictElementsAlmostEqual(
            slices[chinese_slice], {
                'accuracy': 1.0,
                'label/mean': 0.0,
                'my_mean_age': 4.0,
                'my_mean_age_times_label': 0.0,
            })

      util.assert_that(metrics, check_result)

  def testAggregateMultipleSlicesWithSampling(self):
    temp_eval_export_dir = self._getEvalExportDir()
    _, eval_export_dir = linear_classifier.simple_linear_classifier(
        None, temp_eval_export_dir)

    eval_saved_model = load.EvalSavedModel(eval_export_dir)
    eval_shared_model = self.createTestEvalSharedModel(
        eval_saved_model_path=eval_export_dir)

    with beam.Pipeline() as pipeline:
      example1 = self._makeExample(age=3.0, language='english', label=1.0)
      example2 = self._makeExample(age=3.0, language='chinese', label=0.0)
      example3 = self._makeExample(age=4.0, language='english', label=1.0)
      example4 = self._makeExample(age=5.0, language='chinese', label=0.0)

      predict_result_english_slice = (
          eval_saved_model.as_features_predictions_labels(
              eval_saved_model.predict_list(
                  [example1.SerializeToString(),
                   example3.SerializeToString()])))

      predict_result_chinese_slice = (
          eval_saved_model.as_features_predictions_labels(
              eval_saved_model.predict_list(
                  [example2.SerializeToString(),
                   example4.SerializeToString()])))

      test_input = (
          create_test_input(predict_result_english_slice, [(
              ('language', 'english'))]) +
          create_test_input(predict_result_chinese_slice, [(
              ('language', 'chinese'))]) +
          # Overall slice
          create_test_input(
              predict_result_english_slice + predict_result_chinese_slice,
              [()]))
      metrics, _ = (
          pipeline
          | 'CreateTestInput' >> beam.Create(test_input)
          | 'ComputePerSliceMetrics' >> aggregate.ComputePerSliceMetrics(
              eval_shared_model=eval_shared_model,
              desired_batch_size=3,
              compute_confidence_intervals=True))

      def assert_almost_equal_to_value_with_t_distribution(
          target,
          unsampled_value,
          sample_mean,
          sample_standard_deviation,
          sample_degrees_of_freedom,
          delta=2):
        self.assertEqual(target.unsampled_value, unsampled_value)
        self.assertAlmostEqual(target.sample_mean, sample_mean, delta=delta)
        self.assertAlmostEqual(
            target.sample_standard_deviation,
            sample_standard_deviation,
            delta=delta)
        # The possion resampling could return [0, 0, ... ], which will reduce
        # the number of samples.
        self.assertLessEqual(target.sample_degrees_of_freedom,
                             sample_degrees_of_freedom)

      def check_overall_slice(slices):
        my_dict = slices[()]
        assert_almost_equal_to_value_with_t_distribution(
            my_dict['my_mean_age'], 3.75, 3.64, 0.34, 19)
        assert_almost_equal_to_value_with_t_distribution(
            my_dict['accuracy'], 1.0, 1.0, 0, 19)
        assert_almost_equal_to_value_with_t_distribution(
            my_dict['label/mean'], 0.5, 0.59, 0.29, 19)
        assert_almost_equal_to_value_with_t_distribution(
            my_dict['my_mean_age_times_label'], 1.75, 2.15, 1.06, 19)

      def check_english_slice(slices):
        my_dict = slices[(('language', 'english'))]
        assert_almost_equal_to_value_with_t_distribution(
            my_dict['my_mean_age'], 3.5, 3.18, 0.28, 19)
        assert_almost_equal_to_value_with_t_distribution(
            my_dict['accuracy'], 1.0, 1.0, 0, 19)
        assert_almost_equal_to_value_with_t_distribution(
            my_dict['label/mean'], 1.0, 1.0, 0, 19)
        assert_almost_equal_to_value_with_t_distribution(
            my_dict['my_mean_age_times_label'], 3.5, 3.18, 0.28, 19)

      def check_chinese_slice(slices):
        my_dict = slices[(('language', 'chinese'))]
        assert_almost_equal_to_value_with_t_distribution(
            my_dict['my_mean_age'], 4.0, 4.12, 0.83, 19)
        assert_almost_equal_to_value_with_t_distribution(
            my_dict['accuracy'], 1.0, 1.0, 0, 19)
        assert_almost_equal_to_value_with_t_distribution(
            my_dict['label/mean'], 0, 0, 0, 19)
        assert_almost_equal_to_value_with_t_distribution(
            my_dict['my_mean_age_times_label'], 0, 0, 0, 19)

      def check_result(got):
        self.assertEqual(3, len(got), 'got: %s' % got)
        slices = {}
        for slice_key, metrics in got:
          slices[slice_key] = metrics
        check_overall_slice(slices)
        check_english_slice(slices)
        check_chinese_slice(slices)

      util.assert_that(metrics, check_result)


if __name__ == '__main__':
  tf.test.main()
