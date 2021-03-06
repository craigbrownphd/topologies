#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright (c) 2018 Intel Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# SPDX-License-Identifier: EPL-2.0
#

import settings    # Use the custom settings.py file for default parameters
import h5py

import argparse
parser = argparse.ArgumentParser()
parser.add_argument("--use_upsampling",
                    help="use upsampling instead of transposed convolution",
                    action="store_true", default=settings.USE_UPSAMPLING)
parser.add_argument("--num_threads", type=int,
                    default=settings.NUM_INTRA_THREADS,
                    help="the number of intraop threads")
parser.add_argument(
    "--num_inter_threads",
    type=int,
    default=settings.NUM_INTER_THREADS,
    help="the number of interop threads")
parser.add_argument("--batch_size", type=int, default=settings.BATCH_SIZE,
                    help="the batch size for training")
parser.add_argument(
    "--blocktime",
    type=int,
    default=settings.BLOCKTIME,
    help="blocktime")
parser.add_argument("--epochs", type=int, default=settings.EPOCHS,
                    help="number of epochs to train")
parser.add_argument("--patchheight", type=int, default=settings.PATCH_HEIGHT, help="height of patch to train on")
parser.add_argument("--patchwidth", type=int, default=settings.PATCH_WIDTH, help="width of patch to train on")

parser.add_argument(
    "--learningrate",
    type=float,
    default=settings.LEARNING_RATE,
    help="learningrate")
parser.add_argument(
    "--keras_api",
    help="use keras instead of tf.keras",
    action="store_true",
    default=settings.USE_KERAS_API)
parser.add_argument("--channels_first", help="use channels first data format",
                    action="store_true", default=settings.CHANNELS_FIRST)
parser.add_argument("--print_model", help="print the model",
                    action="store_true", default=settings.PRINT_MODEL)
parser.add_argument(
    "--trace",
    help="create a tensorflow timeline trace",
    action="store_true",
    default=settings.CREATE_TRACE_TIMELINE)

parser.add_argument("--out_path", help="TensorBoard logs",
                    default=settings.OUT_PATH)
parser.add_argument("--data_path", help="Data directory",
                    default=settings.DATA_PATH)
parser.add_argument("--mode", type=int, default=settings.MODE,
                    help="Mode 1,2,3,4: entire tumor, active tumor, "
                    "or active core")

parser.add_argument("--num_input_channels", type=int, default=1,
                    help="number of input channels")
parser.add_argument("--num_output_channels", type=int, default=1,
                    help="number of output channels")

args = parser.parse_args()

import os

num_threads = args.num_threads
num_inter_op_threads = args.num_inter_threads

if (args.blocktime > 1000):
    blocktime = "infinite"
else:
    blocktime = str(args.blocktime)

os.environ["CUDA_VISIBLE_DEVICES"]="0"   # Which GPU to use
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "2"  # Get rid of the AVX, SSE warnings

os.environ["KMP_BLOCKTIME"] = blocktime
os.environ["KMP_AFFINITY"] = "compact,1,0,granularity=fine"

os.environ["OMP_NUM_THREADS"] = str(num_threads)
os.environ["INTRA_THREADS"] = str(num_threads)
os.environ["INTER_THREADS"] = str(num_inter_op_threads)
os.environ["KMP_SETTINGS"] = "0"  # Show the settings at runtime

# The timeline trace for TF is saved to this file.
# To view it, run this python script, then load the json file by
# starting Google Chrome browser and pointing the URI to chrome://trace
# There should be a button at the top left of the graph where
# you can load in this json file.
timeline_filename = "timeline_ge_unet_{}_{}_{}.json".format(
    blocktime, num_threads, num_inter_op_threads)

import time
import tensorflow as tf

config = tf.ConfigProto(intra_op_parallelism_threads=num_threads,
                        inter_op_parallelism_threads=num_inter_op_threads)

sess = tf.Session(config=config)

run_options = tf.RunOptions(trace_level=tf.RunOptions.FULL_TRACE)
run_metadata = tf.RunMetadata()  # For Tensorflow trace

if args.channels_first:
    """
    Use NCHW format for data
    """
    concat_axis = 1
    data_format = "channels_first"

else:
    """
    Use NHWC format for data
    """
    concat_axis = -1
    data_format = "channels_last"


print("Data format = " + data_format)
if args.keras_api:
    import keras as K
else:
    from tensorflow import keras as K

K.backend.set_image_data_format(data_format)

import numpy as np
import os

from preprocess import *
import settings


def dice_coef(y_true, y_pred, smooth=1.0):

   numerator = tf.constant(2.) * tf.reduce_sum(y_true * y_pred, axis=(1, 2, 3)) + smooth
   denominator = tf.reduce_sum(y_true + y_pred, axis=(1, 2, 3)) + smooth
   coef = numerator / denominator
   return tf.reduce_mean(coef)


def dice_coef_loss(y_true, y_pred, smooth=1.0):

	loss = -tf.log(tf.constant(2.) * tf.reduce_sum(y_true * y_pred) + smooth) + \
		tf.log(tf.reduce_sum(y_true) + tf.reduce_sum(y_pred) + smooth)
	return loss


def unet_model(args, dropout=0.2, final=False):
    """
    U-Net model definition
    """

    bn = False # Use Batch Normalization

    if args.use_upsampling:
        print("Using UpSampling2D")
    else:
        print("Using Transposed Deconvolution")

    if args.channels_first:
    	inputs = K.layers.Input((args.num_input_channels,None,None),
                                name="Images")
    else:
        inputs = K.layers.Input((None,None,args.num_input_channels),
                                name="Images")

    # Convolution parameters
    params = dict(kernel_size=(3, 3), activation="relu",
                  padding="same", data_format=data_format,
                  kernel_initializer="he_uniform")

    # Transposed convolution parameters
    params_trans = dict(data_format=data_format,
                        kernel_size=(2, 2), strides=(2, 2),
                        padding="same")

    conv1 = K.layers.Conv2D(name="conv1a", filters=32, **params)(inputs)
    if (bn) : conv1 = K.layers.BatchNormalization()(conv1)
    conv1 = K.layers.Conv2D(name="conv1b", filters=32, **params)(conv1)
    if (bn) : conv1 = K.layers.BatchNormalization()(conv1)
    pool1 = K.layers.MaxPooling2D(name="pool1", pool_size=(2, 2))(conv1)

    conv2 = K.layers.Conv2D(name="conv2a", filters=64, **params)(pool1)
    if (bn) : conv2 = K.layers.BatchNormalization()(conv2)
    conv2 = K.layers.Conv2D(name="conv2b", filters=64, **params)(conv2)
    if (bn) : conv2 = K.layers.BatchNormalization()(conv2)
    pool2 = K.layers.MaxPooling2D(name="pool2", pool_size=(2, 2))(conv2)

    conv3 = K.layers.Conv2D(name="conv3a", filters=128, **params)(pool2)
    if (bn) : conv3 = K.layers.BatchNormalization()(conv3)
    conv3 = K.layers.Dropout(dropout)(conv3)
    conv3 = K.layers.Conv2D(name="conv3b", filters=128, **params)(conv3)
    if (bn) : conv3 = K.layers.BatchNormalization()(conv3)

    pool3 = K.layers.MaxPooling2D(name="pool3", pool_size=(2, 2))(conv3)

    conv4 = K.layers.Conv2D(name="conv4a", filters=256, **params)(pool3)
    if (bn) : conv4 = K.layers.BatchNormalization()(conv4)
    conv4 = K.layers.Dropout(dropout)(conv4)
    conv4 = K.layers.Conv2D(name="conv4b", filters=256, **params)(conv4)
    if (bn) : conv4 = K.layers.BatchNormalization()(conv4)

    pool4 = K.layers.MaxPooling2D(name="pool4", pool_size=(2, 2))(conv4)

    conv5 = K.layers.Conv2D(name="conv5a", filters=512, **params)(pool4)
    if (bn) : conv5 = K.layers.BatchNormalization()(conv5)
    conv5 = K.layers.Conv2D(name="conv5b", filters=512, **params)(conv5)
    if (bn) : conv5 = K.layers.BatchNormalization()(conv5)

    if args.use_upsampling:
        up = K.layers.UpSampling2D(name="up6", size=(2, 2))(conv5)
    else:
        up = K.layers.Conv2DTranspose(name="transConv6", filters=256,
                                      **params_trans)(conv5)
    up6 = K.layers.concatenate([up, conv4], axis=concat_axis)

    conv6 = K.layers.Conv2D(name="conv6a", filters=256, **params)(up6)
    if (bn) : conv6 = K.layers.BatchNormalization()(conv6)
    conv6 = K.layers.Conv2D(name="conv6b", filters=256, **params)(conv6)
    if (bn) : conv6 = K.layers.BatchNormalization()(conv6)

    if args.use_upsampling:
        up = K.layers.UpSampling2D(name="up7", size=(2, 2))(conv6)
    else:
        up = K.layers.Conv2DTranspose(name="transConv7", filters=128,
                                      **params_trans)(conv6)
    up7 = K.layers.concatenate([up, conv3], axis=concat_axis)

    conv7 = K.layers.Conv2D(name="conv7a", filters=128, **params)(up7)
    if (bn) : conv7 = K.layers.BatchNormalization()(conv7)
    conv7 = K.layers.Conv2D(name="conv7b", filters=128, **params)(conv7)
    if (bn) : conv7 = K.layers.BatchNormalization()(conv7)

    if args.use_upsampling:
        up = K.layers.UpSampling2D(name="up8", size=(2, 2))(conv7)
    else:
        up = K.layers.Conv2DTranspose(name="transConv8", filters=64,
                                      **params_trans)(conv7)
    up8 = K.layers.concatenate([up, conv2], axis=concat_axis)

    conv8 = K.layers.Conv2D(name="conv8a", filters=64, **params)(up8)
    if (bn) : conv8= K.layers.BatchNormalization()(conv8)
    conv8 = K.layers.Conv2D(name="conv8b", filters=64, **params)(conv8)
    if (bn) : conv8= K.layers.BatchNormalization()(conv8)

    if args.use_upsampling:
        up = K.layers.UpSampling2D(name="up9", size=(2, 2))(conv8)
    else:
        up = K.layers.Conv2DTranspose(name="transConv9", filters=32,
                                      **params_trans)(conv8)
    up9 = K.layers.concatenate([up, conv1], axis=concat_axis)

    conv9 = K.layers.Conv2D(name="conv9a", filters=32, **params)(up9)
    if (bn) : conv9 = K.layers.BatchNormalization()(conv9)
    conv9 = K.layers.Conv2D(name="conv9b", filters=32, **params)(conv9)
    if (bn) : conv9 = K.layers.BatchNormalization()(conv9)

    num_output_channels = 1
    prediction = K.layers.Conv2D(name="PredictionMask",
                                 filters=num_output_channels,
                                 kernel_size=(1, 1),
                                 data_format=data_format,
                                 activation="sigmoid")(conv9)


    tf.summary.image("predictions", prediction, max_outputs=3)

    model = K.models.Model(inputs=[inputs], outputs=[prediction])

    optimizer = K.optimizers.Adam(lr=args.learningrate)

    if final:
        """
        Don't include custom metrics so that we can
        easily reload the model into Keras.
        """
        model.compile(optimizer=optimizer,
              loss="binary_crossentropy",
              metrics=["accuracy"])
    else:

        if args.trace:
            model.compile(optimizer=optimizer,
                          loss=dice_coef_loss,
                          metrics=["accuracy", dice_coef],
                          options=run_options, run_metadata=run_metadata)
        else:
            model.compile(optimizer=optimizer,
                          loss=dice_coef_loss,
                          metrics=["accuracy", dice_coef])

    return model


def get_batch(imgs, msks, batch_size):

    """
    Grab a random set of images/masks. Take a random 128x128 crop from them.
    """

    num_imgs = imgs.shape[0]
    x_len = imgs.shape[1]
    y_len = msks.shape[2]

    # Take random crop from images
    patchx = args.patchheight
    patchy = args.patchwidth

    assert (patchx <= x_len), "Patch is too wide"
    assert (patchy <= y_len), "Patch to too tall"
    
    imgs_batch = np.zeros((batch_size,patchx,patchy,imgs.shape[3]))
    msks_batch = np.zeros((batch_size,patchx,patchy,msks.shape[3]))

    while True:

        count = 0
        for i in np.random.permutation(num_imgs)[:batch_size]:

            if (x_len - patchx) == 0:
                x = 0
                y = 0
            else:
                x = np.random.choice(x_len-patchx)
                y = np.random.choice(y_len-patchy)

            imgs_batch[count] = imgs[i, x:(x+patchx),y:(y+patchy),:]
            msks_batch[count] = msks[i, x:(x+patchx),y:(y+patchy),:]

            if np.random.rand() > 0.5:
                axis = np.random.choice([1, 2])  # Choose axis randomly
                # Random flip
                imgs_batch[count] = np.flip(imgs_batch[count],axis)
                msks_batch[count] = np.flip(msks_batch[count],axis)

                if np.random.rand() > 0.5: # Random 90 degree rotation
                    imgs_batch[count] = np.rot90(imgs_batch[count],1,(0,1))
                    msks_batch[count] = np.rot90(msks_batch[count],1,(0,1))


            count += 1

        yield imgs_batch, msks_batch


def train_and_predict(args):

    print("-" * 40)
    print("Loading and preprocessing train data...")
    print("-" * 40)

    #imgs_train, msks_train = load_data(args.data_path, "_train")
    filename = os.path.join(args.data_path, "processed_data.hdf5")

    hdfFile = h5py.File(filename, "r")
    imgs_train = hdfFile["images/train"][:]
    msks_train = hdfFile["masks/train"][:]

    imgs_train, msks_train = update_channels(imgs_train, msks_train, args)

    print("-" * 40)
    print("Loading and preprocessing test data...")
    print("-" * 40)
    #imgs_test, msks_test = load_data(args.data_path, "_test")

    imgs_test = hdfFile["images/test"][:]
    msks_test = hdfFile["masks/test"][:]

    imgs_test, msks_test = update_channels(imgs_test, msks_test, args)

    print("Train images shape = {}".format(imgs_train.shape))
    print("Train masks shape = {}".format(msks_train.shape))
    print("Test images shape = {}".format(imgs_test.shape))
    print("Test masks shape = {}".format(msks_test.shape))

    print("-" * 30)
    print("Creating and compiling model...")
    print("-" * 30)

    model = unet_model(args)

    if (args.use_upsampling):
        model_fn = os.path.join(args.out_path,
                                "unet_model_upsampling.hdf5")
    else:
        model_fn = os.path.join(args.out_path,
                                "unet_model_transposed.hdf5")

    print("Writing model to '{}'".format(model_fn))

    model_checkpoint = K.callbacks.ModelCheckpoint(model_fn,
                                                   monitor="loss",
                                                   save_best_only=True)

    directoryName = "unet_block{}_inter{}_intra{}".format(blocktime,
                                                          num_threads,
                                                          num_inter_op_threads)

    if (args.use_upsampling):
        tensorboard_checkpoint = K.callbacks.TensorBoard(
            log_dir="{}/batch{}/upsampling_{}".format(args.out_path,
                                                      args.batch_size,
                                                      directoryName),
            write_graph=True)
    else:
        tensorboard_checkpoint = K.callbacks.TensorBoard(
            log_dir="{}/batch{}/transposed_{}".format(args.out_path,
                                                      args.batch_size,
                                                      directoryName),
            write_graph=True)

    print("-" * 30)
    print("Fitting model...")
    print("-" * 30)

    history = K.callbacks.History()

    print("Batch size = {}".format(args.batch_size))

    train_generator = get_batch(imgs_train, msks_train, args.batch_size)

    callbacks = []

    callbacks.append(model_checkpoint)
    callbacks.append(tensorboard_checkpoint)

    # history = model.fit(imgs_train, msks_train,
    #                     epochs=args.epochs,
    #                     batch_size=args.batch_size,
    #                     validation_data=(imgs_test, msks_test),
    #                     callbacks=callbacks)


    history = model.fit_generator(train_generator,
                        steps_per_epoch=imgs_train.shape[0]//args.batch_size,
                        epochs=args.epochs,
                        validation_data=(imgs_test, msks_test),
                        callbacks=callbacks)

    if args.trace:
        """
        Save the training timeline
        """
        from tensorflow.python.client import timeline

        fetched_timeline = timeline.Timeline(run_metadata.step_stats)
        chrome_trace = fetched_timeline.generate_chrome_trace_format()
        with open(timeline_filename, "w") as f:
            print("Saved Tensorflow trace to: {}".format(timeline_filename))
            f.write(chrome_trace)

    print("-" * 30)
    print("Loading the best trained model ...")
    print("-" * 30)
    model = K.models.load_model(
        model_fn, custom_objects={
            "dice_coef_loss": dice_coef_loss,
            "dice_coef": dice_coef})

    # Remove the Dice so that we can load without custom objects
    model.save_weights("weights.h5")
    model = unet_model(args, final=True)  # Model without Dice and custom metrics
    model.load_weights("weights.h5")
    model.save(model_fn)

    print("-" * 30)
    print("Predicting masks on test data...")
    print("-" * 30)
    msks_pred = model.predict(imgs_test, verbose=1)

    print("Saving predictions to file")
    if (args.use_upsampling):
        np.save("msks_pred_upsampling.npy", msks_pred)
    else:
        np.save("msks_pred_transposed.npy", msks_pred)

    start_inference = time.time()
    print("Evaluating model")
    scores = model.evaluate(
        imgs_test,
        msks_test,
        batch_size=args.batch_size,
        verbose=1)

    elapsed_time = time.time() - start_inference
    print("{} images in {:.2f} seconds = {:.3f} images per second inference".format(
        imgs_test.shape[0], elapsed_time, imgs_test.shape[0] / elapsed_time))
    print("Evaluation Scores", scores)


if __name__ == "__main__":

    os.system("lscpu")

    import datetime
    print("Started script on {}".format(datetime.datetime.now()))

    print("args = {}".format(args))
    os.system("uname -a")
    print("TensorFlow version: {}".format(tf.__version__))
    start_time = time.time()

    train_and_predict(args)

    print(
        "Total time elapsed for program = {} seconds".format(
            time.time() -
            start_time))
    print("Stopped script on {}".format(datetime.datetime.now()))
