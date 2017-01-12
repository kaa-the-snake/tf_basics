#!/usr/bin/python
# encoding: utf-8


import tensorflow as tf
import numpy as np
import base64, json, random, sys


all_syms = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZабвгдеёжзийклмнопрстуфхцчшщъыьэюяАБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ \n'\"()?!.,*+-/\%/\\$#@:;".decode("utf-8")
#all_syms = "0123456789abcdefghijklmnopqrstuvwxyzабвгдеёжзийклмнопрстуфхцчшщъыьэюя \n'\"()?!.,*+-/\%/\\$#@:;".decode("utf-8")
#all_syms = "Helo".decode("utf-8")


# read lines, yield symbols
def iterate_messages(path):
    for line in open(path):
        if line and line[-1] == "\n":
            line = line[:-1]
        line = json.loads(base64.b64decode(line))["text"]
        data = []
        for ch in line:
            if ch in all_syms:
                data.append(all_syms.index(ch))
        if data:
            data.append(len(all_syms))      # end-of-message symbol
            yield data


# make all sliding windows
def transform_data_to_sliding_windows(data, window_size):
    result = []
    for i in xrange(len(data) - window_size + 1):
        result.append(np.asarray(sum(data[i : i + window_size], [])))
    return result


# iterate over batches
def iterate_batches(data, max_batch, max_time):
    n = len(data)
    for row in xrange((n + max_batch - 1) / max_batch):
        col = 0
        while True:
            all_zero = True
            batch_data_x, batch_data_y, batch_lengths = [], [], []
            for i in xrange(max_batch):
                if row * max_batch + i < n:
                    k = len(data[row * max_batch + i]) - 1 - col
                    k = max(min(k, max_time), 0)
                    x = data[row * max_batch + i][col : col + k]
                    y = data[row * max_batch + i][col + 1 : col + 1 + k]
                    if k < max_time:
                        x = np.concatenate((x, [0] * (max_time - k)))
                        y = np.concatenate((y, [0] * (max_time - k)))
                    batch_data_x.append(x)
                    batch_data_y.append(y)
                    batch_lengths.append(k)
                    if k != 0:
                        all_zero = False
                else:
                    batch_data_x.append(np.asarray([0] * max_time))
                    batch_data_y.append(np.asarray([0] * max_time))
                    batch_lengths.append(0)
            if all_zero:
                break
            yield batch_data_x, batch_data_y, batch_lengths, float(row) / ((n + max_batch - 1) / max_batch)
            col += max_time



# input shape: batch*time*state
# output shape: batch*time*vocabulary
# multiplies last dimention by `w` and adds `b`
def make_projection(inp, state_size, max_time, vocabulary_size, w, b):
    output = tf.reshape(inp, [-1, state_size])
    output = tf.add(tf.matmul(output, w), b)
    output = tf.reshape(output, [-1, max_time, vocabulary_size])
    return output


def choose_random(distr):
    #print " ".join(map(lambda t: "%.2f" % t, distr))
    cs = np.cumsum(distr)
    s = np.sum(distr)
    k = int(np.searchsorted(cs, np.random.rand(1) * s))
    return min(k, len(distr) - 1)


def make_sample(sess, x, state_x, op, state_op, l, cur_state, seed, max_time, max_sample_length):
    result = ""
    for ch in seed:
        result += (all_syms[ch] if ch < len(all_syms) else "\n")
        res, cur_state = sess.run([op, state_op], feed_dict = {x: [[ch] * max_time], state_x: cur_state, l: [1]})
    #print cur_state.shape
    res = res[0][0]
    #print "\t".join(map(lambda u: "%.2f" % u, res))
    cur_sym = choose_random(res)
    result += all_syms[cur_sym]
    while True:
        res, cur_state = sess.run([op, state_op], feed_dict = {x: [[cur_sym] * max_time], state_x: cur_state, l: [1]})
        res = res[0][0]
        #print "\t".join(map(lambda u: "%.2f" % u, res))
        cur_sym = choose_random(res)
        if cur_sym == len(all_syms) or len(result) >= max_sample_length:
            break
        result += all_syms[cur_sym]
    return result.encode("utf-8")


def print_matr(matr):
    for vec in matr:
        print "\t".join(map(lambda u: "%.5f" % u, vec))


def print_matrs(matrs):
    for matr in matrs:
        print_matr(matr)
        print


def test_projection():
    max_time, state_size, vocabulary_size = 2, 3, 2
    x = tf.placeholder(tf.float32, [None, max_time, state_size])
    w = tf.Variable([[1.0, -1.0], [1.0, -1.0], [1.0, 1.0]])
    b = tf.Variable([1.0, 1.0])
    y = make_projection(x, state_size, max_time, vocabulary_size, w, b)
    init = tf.global_variables_initializer()
    sess = tf.Session()
    sess.run(init)
    print_matrs(sess.run(y, feed_dict = {x: [[[1, 2, 3], [1, 1, 1]]]}))


def test_onehot():
    y = tf.placeholder(tf.int32, [None, 3])
    ohy = tf.one_hot(y, 5, on_value = 1.0)
    init = tf.global_variables_initializer()
    sess = tf.Session()
    sess.run(init)
    print_matrs(sess.run(ohy, feed_dict = {y: [[1, 2, 3], [0, 1, 2], [2, 3, 4]]}))


def test():
    test_projection()
    test_onehot()


def do_train(sess, saver, x_placeholder, y_placeholder, state_placeholder, lengths_placeholder,
             output_operation, state_operation, loss, optimizer,
             zero_state, apply_zero_state,
             data, seed_data,
             batch_size, max_time, max_sample_length, dumps_path, exit_func):
    epoch, prev_loss = 0, 0.0
    while True:
        l, c = 0.0, 0
        cur_state = zero_state
        for batch_x, batch_y, batch_lengths, progress in iterate_batches(data, batch_size, max_time):
            _, cur_state, _l = sess.run([optimizer, state_operation, loss], feed_dict = {x_placeholder: batch_x, y_placeholder: batch_y, state_placeholder: cur_state, lengths_placeholder: batch_lengths})
            l += _l
            c += 1
            print "Progress: %.1f%%, loss: %f" % (progress * 100.0, _l)
            sys.stdout.flush()
        seed = seed_data[min(int(random.random() * len(seed_data)), len(seed_data) - 1)]
        print make_sample(sess, x_placeholder, state_placeholder, output_operation, state_operation, lengths_placeholder, apply_zero_state, seed, max_time, max_sample_length)
        print "Loss: %.5f\tdiff with prev: %.5f\n" % (l / c, l / c - prev_loss)
        sys.stdout.flush()
        saver.save(sess, dumps_path, global_step = epoch)
        epoch += 1
        prev_loss = l / c
        if exit_func(epoch, prev_loss):
            break


# do all stuff
def main():
    # define params
    path = "3be3d3ffd5e6e44608b948109849192b.log"
    vocabulary_size = len(all_syms) + 1

    # read and convert data
    source_data = list(iterate_messages(path))
    data = transform_data_to_sliding_windows(source_data, 5)
    random.shuffle(data)

    # define params
    max_time, batch_size, state_size, learning_rate = 16, len(data), 1024, 0.001

    # create variables and graph
    x = tf.placeholder(tf.int32, [None, max_time])
    lengths = tf.placeholder(tf.int32, [None])
    gru = tf.nn.rnn_cell.GRUCell(state_size)
    w = tf.Variable(tf.random_normal([state_size, vocabulary_size]))
    b = tf.Variable(tf.random_normal([vocabulary_size]))

    # create learning graph
    state_x = tf.placeholder(tf.float32, [None, state_size])
    with tf.variable_scope('train'):
        output, state = tf.nn.dynamic_rnn(gru, tf.one_hot(x, vocabulary_size, on_value = 1.0), initial_state = state_x, sequence_length = lengths, dtype = tf.float32, swap_memory = True)
    output = make_projection(output, state_size, max_time, vocabulary_size, w, b)
    y = tf.placeholder(tf.int32, [None, max_time])

    # define loss and optimizer
    ohy = tf.one_hot(y, vocabulary_size, on_value = 1.0)
    loss = tf.reduce_mean(tf.nn.sigmoid_cross_entropy_with_logits(output, ohy))
    optimizer = tf.train.AdamOptimizer(learning_rate = learning_rate).minimize(loss)

    # renorm output logits for sampling
    apply_output = tf.nn.softmax(output)

    # create saver
    saver = tf.train.Saver(max_to_keep = 100)

    # prepare variables
    init = tf.global_variables_initializer()
    sess = tf.Session()
    sess.run(init)
    zero_state = sess.run(gru.zero_state(batch_size, tf.float32))
    apply_zero_state = sess.run(gru.zero_state(1, tf.float32))

    # apply mode
    if len(sys.argv) > 1:
        saver.restore(sess, sys.argv[1])
        while True:
            seed = raw_input("Enter phrase: ")
            print make_sample(sess, x, state_x, apply_output, state, lengths, apply_zero_state, seed, max_time, 1000)
    else:
        # training mode
        do_train(sess, saver, x, y, state_x, lengths,
             apply_output, state, loss, optimizer,
             zero_state, apply_zero_state,
             data, source_data,
             batch_size, max_time, 1000, "dumps/dump",
             lambda epoch, epoch_loss: False)


# entry point
if __name__ == "__main__":
    #test()
    main()

