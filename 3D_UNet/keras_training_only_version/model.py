#!/usr/bin/python

# ----------------------------------------------------------------------------
# Copyright 2018 Intel
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ----------------------------------------------------------------------------

import os.path
import numpy as np

import tensorflow as tf

import keras as K


def dice_coef(y_true, y_pred, axis=(1, 2, 3), smooth=1.):
    """
    Sorenson (Soft) Dice
    2 * |TP| / |T|*|P|
    where T is ground truth mask and P is the prediction mask
    """
    intersection = tf.reduce_sum(y_true * y_pred, axis=axis)
    union = tf.reduce_sum(y_true + y_pred, axis=axis)
    numerator = tf.constant(2.) * intersection + smooth
    denominator = union + smooth
    coef = numerator / denominator

    return tf.reduce_mean(coef)


def dice_coef_loss(target, prediction, axis=(1, 2, 3), smooth=1.):
    """
    Sorenson (Soft) Dice loss
    Using -log(Dice) as the loss since it is better behaved.
    Also, the log allows avoidance of the division which
    can help prevent underflow when the numbers are very small.
    """
    intersection = tf.reduce_sum(prediction * target, axis=axis)
    p = tf.reduce_sum(prediction, axis=axis)
    t = tf.reduce_sum(target, axis=axis)
    numerator = tf.reduce_mean(intersection + smooth)
    denominator = tf.reduce_mean(t + p + smooth)
    dice_loss = -tf.log(2.*numerator) + tf.log(denominator)

    return dice_loss


def combined_dice_ce_loss(target, prediction, axis=(1, 2, 3), smooth=1., weight=.7):
    """
    Combined Dice and Binary Cross Entropy Loss
    """
    return weight*dice_coef_loss(target, prediction, axis, smooth) + \
        (1-weight)*K.losses.binary_crossentropy(target, prediction)


CHANNEL_LAST = True
if CHANNEL_LAST:
    concat_axis = -1
    data_format = "channels_last"

else:
    concat_axis = 1
    data_format = "channels_first"


def unet_3d(input_shape, use_upsampling=False, learning_rate=0.001,
            n_cl_in=1, n_cl_out=1, dropout=0.2, print_summary=False):
    """
    3D U-Net
    """


    inputs = K.layers.Input(shape=input_shape, name="Input_Image")

    params = dict(kernel_size=(3, 3, 3), activation=None,
                  padding="same", data_format=data_format,
                  kernel_initializer="he_uniform",
                  kernel_regularizer=K.regularizers.l2(1e-5))

    conv1 = K.layers.Conv3D(name="conv1a", filters=32, **params)(inputs)
    conv1 = K.layers.BatchNormalization()(conv1)
    conv1 = K.layers.Activation("relu")(conv1)
    conv1 = K.layers.Conv3D(name="conv1b", filters=64, **params)(conv1)
    conv1 = K.layers.BatchNormalization()(conv1)
    conv1 = K.layers.Activation("relu")(conv1)
    pool1 = K.layers.MaxPooling3D(name="pool1", pool_size=(2, 2, 2))(conv1)

    conv2 = K.layers.Conv3D(name="conv2a", filters=64, **params)(pool1)
    conv2 = K.layers.BatchNormalization()(conv2)
    conv2 = K.layers.Activation("relu")(conv2)
    conv2 = K.layers.Conv3D(name="conv2b", filters=128, **params)(conv2)
    conv2 = K.layers.BatchNormalization()(conv2)
    conv2 = K.layers.Activation("relu")(conv2)
    pool2 = K.layers.MaxPooling3D(name="pool2", pool_size=(2, 2, 2))(conv2)

    conv3 = K.layers.Conv3D(name="conv3a", filters=128, **params)(pool2)
    conv3 = K.layers.BatchNormalization()(conv3)
    conv3 = K.layers.Activation("relu")(conv3)
    # Trying dropout layers earlier on, as indicated in the paper
    conv3 = K.layers.SpatialDropout3D(dropout)(conv3)
    conv3 = K.layers.Conv3D(name="conv3b", filters=256, **params)(conv3)
    conv3 = K.layers.BatchNormalization()(conv3)
    conv3 = K.layers.Activation("relu")(conv3)
    pool3 = K.layers.MaxPooling3D(name="pool3", pool_size=(2, 2, 2))(conv3)

    conv4 = K.layers.Conv3D(name="conv4a", filters=256, **params)(pool3)
    conv4 = K.layers.BatchNormalization()(conv4)
    conv4 = K.layers.Activation("relu")(conv4)
    # Trying dropout layers earlier on, as indicated in the paper
    conv4 = K.layers.SpatialDropout3D(dropout)(conv4)

    conv4 = K.layers.Conv3D(name="conv4b", filters=512, **params)(conv4)
    conv4 = K.layers.BatchNormalization()(conv4)
    conv4 = K.layers.Activation("relu")(conv4)

    if use_upsampling:
        up = K.layers.UpSampling3D(name="up4", size=(2, 2, 2))(conv4)
    else:
        up = K.layers.Conv3DTranspose(name="transConv4", filters=512,
                                      data_format=data_format,
                                      kernel_size=(2, 2, 2),
                                      strides=(2, 2, 2),
                                      kernel_regularizer=K.regularizers.l2(
                                          1e-5),
                                      padding="same")(conv4)

    up4 = K.layers.concatenate([up, conv3], axis=concat_axis)

    conv5 = K.layers.Conv3D(name="conv5a", filters=256, **params)(up4)
    conv5 = K.layers.BatchNormalization()(conv5)
    conv5 = K.layers.Activation("relu")(conv5)
    conv5 = K.layers.Conv3D(name="conv5b", filters=256, **params)(conv5)
    conv5 = K.layers.BatchNormalization()(conv5)
    conv5 = K.layers.Activation("relu")(conv5)

    if use_upsampling:
        up = K.layers.UpSampling3D(name="up5", size=(2, 2, 2))(conv5)
    else:
        up = K.layers.Conv3DTranspose(name="transConv5",
                                      filters=256, data_format=data_format,
                                      kernel_size=(2, 2, 2),
                                      strides=(2, 2, 2),
                                      kernel_regularizer=K.regularizers.l2(
                                          1e-5),
                                      padding="same")(conv5)

    up5 = K.layers.concatenate([up, conv2], axis=concat_axis)

    conv6 = K.layers.Conv3D(name="conv6a", filters=128, **params)(up5)
    conv6 = K.layers.BatchNormalization()(conv6)
    conv6 = K.layers.Activation("relu")(conv6)
    conv6 = K.layers.Conv3D(name="conv6b", filters=128, **params)(conv6)
    conv6 = K.layers.BatchNormalization()(conv6)
    conv6 = K.layers.Activation("relu")(conv6)

    if use_upsampling:
        up = K.layers.UpSampling3D(name="up6", size=(2, 2, 2))(conv6)
    else:
        up = K.layers.Conv3DTranspose(name="transConv6",
                                      filters=128, data_format=data_format,
                                      kernel_size=(2, 2, 2),
                                      strides=(2, 2, 2),
                                      kernel_regularizer=K.regularizers.l2(
                                          1e-5),
                                      padding="same")(conv6)

    up6 = K.layers.concatenate([up, conv1], axis=concat_axis)

    conv7 = K.layers.Conv3D(name="conv7a", filters=64, **params)(up6)
    conv7 = K.layers.BatchNormalization()(conv7)
    conv7 = K.layers.Activation("relu")(conv7)
    conv7 = K.layers.Conv3D(name="conv7b", filters=64, **params)(conv7)
    conv7 = K.layers.BatchNormalization()(conv7)
    conv7 = K.layers.Activation("relu")(conv7)
    pred = K.layers.Conv3D(name="Prediction_Mask", filters=n_cl_out,
                           kernel_size=(1, 1, 1),
                           data_format=data_format,
                           kernel_regularizer=K.regularizers.l2(1e-5),
                           activation="sigmoid")(conv7)

    model = K.models.Model(inputs=[inputs], outputs=[pred])

    if print_summary:
        model.summary()

    opt = K.optimizers.Adam(learning_rate)

    return model, opt


def sensitivity(target, prediction, axis=(1, 2, 3), smooth=1.):
    """
    Sensitivity
    """
    intersection = tf.reduce_sum(prediction * target, axis=axis)
    coef = (intersection + smooth) / (tf.reduce_sum(target,
                                                    axis=axis) + smooth)
    return tf.reduce_mean(coef)


def specificity(target, prediction, axis=(1, 2, 3), smooth=1.):
    """
    Specificity
    """
    intersection = tf.reduce_sum(prediction * target, axis=axis)
    coef = (intersection + smooth) / (tf.reduce_sum(prediction,
                                                    axis=axis) + smooth)
    return tf.reduce_mean(coef)
