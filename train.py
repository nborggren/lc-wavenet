"""Training script for the WaveNet network on the VCTK corpus.

This script trains a network with the WaveNet using data from the VCTK corpus,
which can be freely downloaded at the following site (~10 GB):
http://homepages.inf.ed.ac.uk/jyamagis/page3/page58/page58.html
"""

from __future__ import print_function

import argparse
from datetime import datetime
import json
import os
import sys
import time

# Install memory_util
#from urllib2 import urlopen
#response = urlopen("https://raw.githubusercontent.com/yaroslavvb/memory_util/master/memory_util.py")
#open("memory_util.py", "wb").write(response.read())
#
#import memory_util
#memory_util.vlog(1)
# End install

import tensorflow as tf
from tensorflow.python.client import timeline

from wavenet import WaveNetModel,LCAudioReader, optimizer_factory


BATCH_SIZE = 1
LOGDIR_ROOT = './logdir'
DATA_DIR = None
CHECKPOINT_EVERY = 50
NUM_STEPS = int(1e5)
LEARNING_RATE = 1e-3
WAVENET_PARAMS = './wavenet_params.json'
STARTED_DATESTRING = "{0:%Y-%m-%dT%H-%M-%S}".format(datetime.now())
SAMPLE_SIZE = 100000
L2_REGULARIZATION_STRENGTH = 0
SILENCE_THRESHOLD = None
EPSILON = 0.001
MOMENTUM = 0.9
MAX_TO_KEEP = 5
METADATA = False


def get_arguments():
	parser = argparse.ArgumentParser(description = 'WaveNet example network')
	
	parser.add_argument('--batch-size',
						type = int,
						default = BATCH_SIZE,
						help = 'How many wav files to process at once. Default: ' + str(BATCH_SIZE) + '.')
	
	parser.add_argument('--data-dir',
						type = str,
						default = DATA_DIR,
						help = 'The directory containing training WAV data and any LC files if LC enabled. Default: None. Expects: path')
	
	parser.add_argument('--store-metadata',
						type = bool,
						default = METADATA,
						help = 'Whether to store advanced debugging information '
								'(execution time, memory consumption) for use with '
								'TensorBoard. Default: ' + str(METADATA) + '.')
	
	parser.add_argument('--logdir',
		type = str,
		default = None,
		help = 'Directory in which to store the logging '
				'information for TensorBoard. '
				'If the model already exists, it will restore '
				'the state and will continue training. '
				'Cannot use with --logdir_root and --restore_from.')
	
	parser.add_argument('--logdir-root',
		type = str,
		default = None,
		help = 'Root directory to place the logging '
				'output and generated model. These are stored '
				'under the dated subdirectory of --logdir_root. '
				'Cannot use with --logdir.')
	
	parser.add_argument('--restore-from',
		type = str,
		default = None,
		help = 'Directory in which to restore the model from. '
				'This creates the new model under the dated directory '
				'in --logdir_root. '
				'Cannot use with --logdir.')
	
	parser.add_argument('--checkpoint-every',
		type = int,
		default = CHECKPOINT_EVERY,
		help = 'How many steps to save each checkpoint after. Default: ' + str(CHECKPOINT_EVERY) + '.')
	
	parser.add_argument('--num-steps',
		type = int,
		default = NUM_STEPS,
		help = 'Number of training steps. Default: ' + str(NUM_STEPS) + '. Expects: int')
	
	parser.add_argument('--learning-rate',
		type = float,
		default = LEARNING_RATE,
		help = 'Learning rate for training. Default: ' + str(LEARNING_RATE) + '. Expects: float32')

	parser.add_argument('--wavenet-params',
		type = str,
		default = WAVENET_PARAMS,
		help = 'JSON file with the network parameters. Default: ' + WAVENET_PARAMS + '. Expects: string')

	parser.add_argument('--sample-size',
		type = int,
		default = SAMPLE_SIZE,
		help = 'Concatenate and cut audio samples to this many '
		'samples. Default: ' + str(SAMPLE_SIZE) + '. Expects: int')
	
	parser.add_argument('--l2-regularization-strength',
		type = float,
		default = L2_REGULARIZATION_STRENGTH,
		help = 'Coefficient in the L2 regularization. '
		'Default: False. Expects: float32')

	parser.add_argument('--silence-threshold',
		type = float,
		default = SILENCE_THRESHOLD,
		help = 'Volume threshold below which to trim the start '
		'and the end from the training set samples. Default: ' + str(SILENCE_THRESHOLD) + '. Expects: int')
	
	parser.add_argument('--optimizer',
		type = str,
		default = 'adam',
		choices = optimizer_factory.keys(),
		help = 'Select the optimizer specified by this option. Default: adam. Expects: string')
	
	parser.add_argument('--momentum',
		type = float,
		default = MOMENTUM,
		help = 'Specify the momentum to be '
		'used by sgd or rmsprop optimizer. Ignored by the '
		'adam optimizer. Default: ' + str(MOMENTUM) + '. Expects: float32')
	
	parser.add_argument('--histograms',
		action = 'store_true',
		help = 'Whether to store histogram summaries. Default: False')
	
	parser.add_argument('--gc-channels',
		type = int,
		default = None,
		help = 'Number of global condition channels. Default: None. Expecting: int')

	parser.add_argument('--initial-lc-channels',
		type = int,
		default = None,
		help = "Number of local conditioning channels. Default: None. Expecting: int")
	
	parser.add_argument('--lc-channels',
		type = int,
		default = None,
		help = "Number of local conditioning channels. Default: None. Expecting: int")

	parser.add_argument('--lc-fileformat',
		type = str,
		default = None,
		help = "Extension of files being used for local conditioning. Default: None. Expecting: string")

	parser.add_argument('--max-checkpoints',
		type = int,
		default = MAX_TO_KEEP,
		help = 'Maximum amount of checkpoints that will be kept alive. Default: ' + str(MAX_TO_KEEP) + '.')
	
	return parser.parse_args()


def save(saver, sess, logdir, step):
	# TODO: Make this model name such that its name is $(hyper_param_string).ckpt
	model_name = 'model.ckpt'
	checkpoint_path = os.path.join(logdir, model_name)
	print('Storing checkpoint to {} ... '.format(logdir))

	if not os.path.exists(logdir):
		os.makedirs(logdir)

	saver.save(sess, checkpoint_path, global_step = step)
	print('Done.')


def load(saver, sess, logdir):
	print("Trying to restore saved checkpoints from {} ...".format(logdir), end = "")

	ckpt = tf.train.get_checkpoint_state(logdir)
	if ckpt:
		print("  Checkpoint found: {}".format(ckpt.model_checkpoint_path))
		global_step = int(ckpt.model_checkpoint_path
						  .split('/')[-1]
						  .split('-')[-1])
		print("  Global step was: {}".format(global_step))
		print("  Restoring...", end="")
		saver.restore(sess, ckpt.model_checkpoint_path)
		print(" Done.")
		return global_step
	else:
		print(" No checkpoint found.")
		return None


def get_default_logdir(logdir_root):
	logdir = os.path.join(logdir_root, 'train', STARTED_DATESTRING)
	return logdir


def validate_directories(args):
	"""Validate and arrange directory related arguments."""

	# Validation
	if args.logdir and args.logdir_root:
		raise ValueError("--logdir and --logdir_root cannot be "
						 "specified at the same time.")

	if args.logdir and args.restore_from:
		raise ValueError(
			"--logdir and --restore_from cannot be specified at the same "
			"time. This is to keep your previous model from unexpected "
			"overwrites.\n"
			"Use --logdir_root to specify the root of the directory which "
			"will be automatically created with current date and time, or use "
			"only --logdir to just continue the training from the last "
			"checkpoint.")

	# Arrangement
	logdir_root = args.logdir_root
	if logdir_root is None:
		logdir_root = LOGDIR_ROOT

	logdir = args.logdir
	if logdir is None:
		logdir = get_default_logdir(logdir_root)
		print('Using default logdir: {}'.format(logdir))

	restore_from = args.restore_from
	if restore_from is None:
		# args.logdir and args.restore_from are exclusive,
		# so it is guaranteed the logdir here is newly created.
		restore_from = logdir

	return {
		'logdir': logdir,
		'logdir_root': args.logdir_root,
		'restore_from': restore_from
	}


def main():
	args = get_arguments()

	try:
		directories = validate_directories(args)
	except ValueError as e:
		print("Some arguments are wrong:")
		print(str(e))
		return

	logdir = directories['logdir']
	restore_from = directories['restore_from']

	# Even if we restored the model, we will treat it as new training
	# if the trained model is written into an arbitrary location.
	is_overwritten_training = logdir != restore_from

	with open(args.wavenet_params, 'r') as f:
		wavenet_params = json.load(f)

	# Create coordinator.
	coord = tf.train.Coordinator()

	# create session
	sess = tf.Session(config = tf.ConfigProto(log_device_placement = False))

	# Load raw waveform from VCTK corpus.
	with tf.name_scope('create_inputs'):
		# Allow silence trimming to be skipped by specifying a threshold near
		# zero.
		if args.silence_threshold is None:
			silence_threshold = None
		else:
			silence_threshold = args.silence_threshold \
								if args.silence_threshold > EPSILON \
								else None

		gc_enabled = args.gc_channels is not None
		lc_enabled = args.lc_channels is not None

		initial_lc_channels = args.initial_lc_channels if lc_enabled else None
		lc_channels = args.lc_channels if lc_enabled else None
		lc_fileformat = args.lc_fileformat if lc_enabled else None

		if lc_enabled and initial_lc_channels is None:
			raise ValueError("Inital LC channels must be specified when local conditioning is enabled.")



		# LC channels are non-zero but no format is specifid
		if lc_enabled and args.lc_fileformat is None:
			raise ValueError("LC file format must be specified when local conditioning is enabled.")

		if args.lc_fileformat is not None and not lc_enabled:
			raise ValueError("LC channels have to be set when a LC file format is specified.")
		
		reader = LCAudioReader(data_dir = args.data_dir,
							   coord = coord,
							   receptive_field = WaveNetModel.calculate_receptive_field(
									wavenet_params["filter_width"],
									wavenet_params["dilations"],
									wavenet_params["scalar_input"],
									wavenet_params["initial_filter_width"]),
							   gc_enabled = gc_enabled,
							   lc_enabled = lc_enabled,
							   lc_channels = initial_lc_channels,
							   lc_fileformat = args.lc_fileformat,
							   sample_rate = wavenet_params['sample_rate'],
							   sample_size = args.sample_size,
							   silence_threshold = silence_threshold,
							   sess = sess)
		# dequeue audio samples
		audio_batch = reader.dq_audio(args.batch_size)

		# dequeue gc embeddings
		if gc_enabled:
			gc_id_batch = reader.dq_gc(args.batch_size)
		else:
			gc_id_batch = None

		# dequeue lc embeddings
		if lc_enabled:
			lc_encoded_batch = reader.dq_lc(args.batch_size) 
		else:
			lc_encoded_batch = None
		

	# Create network.
	net = WaveNetModel(
		batch_size = args.batch_size,
		dilations = wavenet_params["dilations"],
		filter_width = wavenet_params["filter_width"],
		residual_channels = wavenet_params["residual_channels"],
		dilation_channels = wavenet_params["dilation_channels"],
		skip_channels = wavenet_params["skip_channels"],
		quantization_channels = wavenet_params["quantization_channels"],
		use_biases = wavenet_params["use_biases"],
		scalar_input = wavenet_params["scalar_input"],
		initial_filter_width = wavenet_params["initial_filter_width"],
		histograms = args.histograms,
		gc_channels = args.gc_channels,
		gc_cardinality = reader.get_gc_cardinality(),
		initial_lc_channels = initial_lc_channels,
		lc_channels = lc_channels)


	if args.l2_regularization_strength == 0:
		args.l2_regularization_strength = None

	# create loss
	loss = net.loss(input_batch = audio_batch,
					gc_batch = gc_id_batch,
					lc_encoded_batch = lc_encoded_batch,
					l2_regularization_strength = args.l2_regularization_strength)

	# create optimizer
	optimizer = optimizer_factory[args.optimizer](
					learning_rate = args.learning_rate,
					momentum = args.momentum)

	# set up optimizer with trainable vars
	trainable = tf.trainable_variables()
	optim = optimizer.minimize(loss, var_list = trainable)

	# set up logging for TensorBoard.
	writer = tf.summary.FileWriter(logdir)
	writer.add_graph(tf.get_default_graph())
	run_metadata = tf.RunMetadata()
	summaries = tf.summary.merge_all()

	# set up session initial state
	init = tf.global_variables_initializer()
#	with memory_util.capture_stderr() as stderr:
	sess.run(init)
	saved_vars = [v.name for v in tf.global_variables()]
	json.dump(saved_vars, open(os.path.join(logdir, 'saved_vars.txt'), 'w'))

#	memory_util.print_memory_timeline(stderr, ignore_less_than_bytes=1000)

	# saver for storing checkpoints of the model.
	# saver = tf.train.Saver(var_list = tf.trainable_variables(), max_to_keep = args.max_checkpoints)
	saver = tf.train.Saver( max_to_keep = args.max_checkpoints)

	# try loading pre-existing model
	try:
		saved_global_step = load(saver, sess, restore_from)
		if is_overwritten_training or saved_global_step is None:
			# The first training step will be saved_global_step + 1,
			# therefore we put -1 here for new or overwritten trainings.
			saved_global_step = -1
	except:
		print("Something went wrong while restoring checkpoint. "
			  "We will terminate training to avoid accidentally overwriting "
			  "the previous model.")
		raise

	# start audio reader threads
	threads = tf.train.start_queue_runners(sess = sess, coord = coord)
	reader.start_threads()

	
	step = None
	last_saved_step = saved_global_step
	try:
		for step in range(saved_global_step + 1, args.num_steps):
			start_time = time.time()
			if args.store_metadata and step % 50 == 0:
				# Slow run that stores extra information for debugging.
				print('Storing metadata')
				run_options = tf.RunOptions(
					trace_level = tf.RunOptions.FULL_TRACE)

				summary, loss_value, _ = sess.run(
					[summaries, loss, optim],
					options = run_options,
					run_metadata = run_metadata)

				writer.add_summary(summary, step)
				writer.add_run_metadata(run_metadata,
										'step_{:04d}'.format(step))
				tl = timeline.Timeline(run_metadata.step_stats)
				timeline_path = os.path.join(logdir, 'timeline.trace')
				with open(timeline_path, 'w') as f:
					f.write(tl.generate_chrome_trace_format(show_memory = True))
			else:
				summary, loss_value, _ = sess.run([summaries, loss, optim])

				writer.add_summary(summary, step)

			duration = time.time() - start_time
			print('step {:d} - loss = {:.3f}, ({:.3f} sec/step)'
				  .format(step, loss_value, duration))

			if step % args.checkpoint_every == 0:
				save(saver, sess, logdir, step)
				last_saved_step = step

	except KeyboardInterrupt:
		# Introduce a line break after ^C is displayed so save message
		# is on its own line.
		print()
	finally:
		if step > last_saved_step:
			save(saver, sess, logdir, step)

		coord.request_stop()
		coord.join(threads)


if __name__ == '__main__':
	main()
